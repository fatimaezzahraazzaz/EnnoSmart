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

from dotenv import load_dotenv


# ============================================================
# ENV
# ============================================================

def _load_project_env() -> None:
    candidates = [
        Path.cwd() / ".env",
        Path.cwd().parent / ".env",
        Path(r"C:\EnnoSmart\.env"),
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
    """
    Variante legacy : conserve '-' si un ancien dossier existe avec tiret.
    """
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
    return hashlib.sha1(clean_text(text).encode("utf-8", errors="ignore")).hexdigest()[:16]


def split_long_text(text: str, max_chars: int = 2500) -> List[str]:
    text = clean_text(text)
    if len(text) <= max_chars:
        return [text] if text else []

    sentences = re.split(r"(?<=[.!?])\s+", text)
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
    for ch in chunks:
        if len(ch) <= max_chars:
            fixed.append(ch)
        else:
            for i in range(0, len(ch), max_chars):
                part = ch[i : i + max_chars].strip()
                if part:
                    fixed.append(part)
    return fixed


def _read_json_safe(path: Path, default: Any = None) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        pass
    return default


# ============================================================
# Loader STRICT : contenu extrait uniquement, avant LLM
# ============================================================

class EnnoExtractedContentLoader:
    """
    Charge uniquement des passages issus du contenu extrait ou des passages NLP source.

    Corrections :
    - résout automatiquement ai-radar / ai_radar / AI_RADAR ;
    - cherche aussi dans years/<année>/nlp et years/<année>/rag ;
    - si aucun nlp legacy n'existe, prend la dernière année disponible ;
    - exclut diagnostic_ennodiagnostic.json et les champs générés.
    """

    SOURCE_TEXT_KEYS = [
        "raw_text",
        "original_text",
        "source_text",
        "extracted_text",
        "text",
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
    ):
        _load_project_env()

        self.organisme_input = str(organisme or "")
        self.project_input = str(project or "")
        self.year = str(year or os.getenv("ENNOSMART_PROJECT_YEAR", "") or "").strip()

        self.organisme = safe_name(organisme)
        self.project = safe_name(project)

        self.base_dir = base_dir or Path(_str_env("ENNOSMART_BASE_DIR", r"C:\EnnoSmart"))
        self.storage_root = self.base_dir / "storage" / "organismes"

        if allow_rag_fallback is None:
            allow_rag_fallback = os.getenv("AI_DETECTOR_ALLOW_RAG_FALLBACK", "1") == "1"
        self.allow_rag_fallback = bool(allow_rag_fallback)

        self.project_root = self._resolve_project_root()
        self.year_root = self._resolve_year_root()

        # Ordre important : année courante d'abord, puis legacy.
        self.nlp_paths = self._candidate_nlp_paths()
        self.rag_chunks_paths = self._candidate_rag_paths()
        self.processed_dirs = self._candidate_processed_dirs()

        self.loader_report: Dict[str, Any] = {
            "input_policy": "extracted_content_before_llm_only",
            "organisme_input": self.organisme_input,
            "project_input": self.project_input,
            "year": self.year,
            "organisme_id": self.organisme,
            "project_id": self.project,
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
                "Les champs reformulation/summary/resume/diagnostic/llm_output sont ignorés.",
                "Le score IA porte sur les textes sources extraits ou les passages NLP source.",
                "Correction active : résolution automatique ai-radar / ai_radar / years/<année>.",
            ],
        }

    # --------------------------
    # Résolution chemins robuste
    # --------------------------

    def _resolve_project_root(self) -> Path:
        org_variants = _name_variants(self.organisme_input)
        project_variants = _name_variants(self.project_input)

        candidates: List[Path] = []
        for org in org_variants:
            for proj in project_variants:
                candidates.append(self.storage_root / org / "projects" / proj)

        # Priorité aux dossiers contenant nlp/rag/years.
        existing = [p for p in candidates if p.exists()]
        for p in existing:
            if (p / "years").exists() or (p / "nlp").exists() or (p / "rag").exists():
                return p

        if existing:
            return existing[0]

        # Recherche plus souple si casse différente.
        for org_dir in self.storage_root.glob("*"):
            if not org_dir.is_dir():
                continue
            if safe_name(org_dir.name) != self.organisme:
                continue
            projects_dir = org_dir / "projects"
            if not projects_dir.exists():
                continue
            for proj_dir in projects_dir.glob("*"):
                if proj_dir.is_dir() and safe_name(proj_dir.name) == self.project:
                    return proj_dir

        return self.storage_root / self.organisme / "projects" / self.project

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

        # Choisir la plus récente contenant nlp/rag/documents.
        def year_score(p: Path) -> Tuple[int, str]:
            try:
                y = int(re.findall(r"\d{4}", p.name)[0])
            except Exception:
                y = 0
            has_data = int((p / "nlp").exists() or (p / "rag").exists() or (p / "documents").exists())
            return (has_data, f"{y:04d}")

        year_dirs = sorted(year_dirs, key=year_score, reverse=True)
        return year_dirs[0]

    def _candidate_nlp_paths(self) -> List[Path]:
        paths: List[Path] = []
        if self.year_root:
            paths.append(self.year_root / "nlp" / "nlp_result.json")
        paths.append(self.project_root / "nlp" / "nlp_result.json")
        # fallback toutes années
        years_dir = self.project_root / "years"
        if years_dir.exists():
            for y in sorted(years_dir.glob("*"), reverse=True):
                paths.append(y / "nlp" / "nlp_result.json")
        return list(dict.fromkeys(paths))

    def _candidate_rag_paths(self) -> List[Path]:
        paths: List[Path] = []
        if self.year_root:
            paths.append(self.year_root / "rag" / "chunks.json")
        paths.append(self.project_root / "rag" / "chunks.json")
        years_dir = self.project_root / "years"
        if years_dir.exists():
            for y in sorted(years_dir.glob("*"), reverse=True):
                paths.append(y / "rag" / "chunks.json")
        return list(dict.fromkeys(paths))

    def _candidate_processed_dirs(self) -> List[Path]:
        paths: List[Path] = []
        if self.year_root:
            paths.append(self.year_root / "documents" / "processed")
        paths.append(self.project_root / "documents" / "processed")
        years_dir = self.project_root / "years"
        if years_dir.exists():
            for y in sorted(years_dir.glob("*"), reverse=True):
                paths.append(y / "documents" / "processed")
        return list(dict.fromkeys(paths))

    # --------------------------
    # Load passages
    # --------------------------

    def load_passages(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        passages: List[Dict[str, Any]] = []

        nlp_loaded = False
        for nlp_path in self.nlp_paths:
            if nlp_path.exists():
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
            processed_passages: List[Dict[str, Any]] = []
            for d in self.processed_dirs:
                loaded = self._load_from_processed_dir(d)
                processed_passages.extend(loaded)
                self.loader_report["used_sources"].append(
                    {
                        "source": "documents_processed",
                        "path": str(d),
                        "passages_loaded": len(loaded),
                    }
                )
                if loaded:
                    break
            passages.extend(processed_passages)

        if not passages and self.allow_rag_fallback:
            for rag_path in self.rag_chunks_paths:
                if rag_path.exists():
                    rag_passages = self._load_from_rag_chunks(rag_path)
                    passages.extend(rag_passages)
                    self.loader_report["used_sources"].append(
                        {
                            "source": "rag_chunks_source_fallback",
                            "path": str(rag_path),
                            "passages_loaded": len(rag_passages),
                            "warning": "fallback accepté uniquement si les chunks sont construits depuis les sources extraites",
                        }
                    )
                    if rag_passages:
                        break

        passages = self._dedupe_passages(passages)

        self.loader_report["nlp_found"] = nlp_loaded
        self.loader_report["total_passages_after_dedupe"] = len(passages)
        self.loader_report["documents_covered"] = sorted(
            list({str(p.get("document", "")) for p in passages if p.get("document")})
        )
        self.loader_report["documents_covered_count"] = len(self.loader_report["documents_covered"])

        return passages, self.loader_report

    def _is_generated_like(self, item: Dict[str, Any]) -> bool:
        if not isinstance(item, dict):
            return False
        joined = " ".join(
            str(item.get(k, ""))
            for k in ["source", "origin", "content_origin", "text_origin", "generation_origin", "created_by", "producer"]
        ).lower()
        bad = ["llm", "generated", "reformulation", "diagnostic", "gemini", "openrouter", "chatgpt"]
        # Ne pas exclure "synthetic" car dans les projets IA/Radar, "données synthétiques" est un contenu métier source.
        return any(m in joined for m in bad)

    def _extract_source_text_from_item(self, item: Any) -> str:
        if isinstance(item, str):
            return clean_text(item)
        if not isinstance(item, dict):
            return ""
        if self._is_generated_like(item):
            return ""

        parts: List[str] = []

        for key in self.SOURCE_TEXT_KEYS:
            if key in self.GENERATED_TEXT_KEYS:
                continue
            value = item.get(key)
            if isinstance(value, str):
                txt = clean_text(value)
                if len(txt) > 20:
                    parts.append(txt)

        for key in ["evidence", "source", "sources", "supporting_passages", "preuves"]:
            value = item.get(key)
            if isinstance(value, str):
                txt = clean_text(value)
                if len(txt) > 20:
                    parts.append(txt)
            elif isinstance(value, list):
                for sub in value:
                    sub_text = self._extract_source_text_from_item(sub)
                    if sub_text:
                        parts.append(sub_text)
            elif isinstance(value, dict):
                sub_text = self._extract_source_text_from_item(value)
                if sub_text:
                    parts.append(sub_text)

        return clean_text(" ".join(parts))

    def _get_doc_name(self, item: Dict[str, Any], default: str = "") -> str:
        if not isinstance(item, dict):
            return default
        return str(
            item.get("document")
            or item.get("file_name")
            or item.get("source_document")
            or item.get("filename")
            or item.get("name")
            or default
            or ""
        )

    def _get_section(self, item: Dict[str, Any]) -> str:
        if not isinstance(item, dict):
            return ""
        return str(item.get("section_title") or item.get("section") or item.get("title") or "")

    def _load_from_nlp(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        passages: List[Dict[str, Any]] = []
        if not isinstance(data, dict):
            return passages

        # Documents complets
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
                passages.append(
                    {
                        "passage_id": str(doc.get("id") or doc.get("document_id") or f"{doc_key}_{idx}"),
                        "pack": "document_extrait",
                        "role": "document_extrait",
                        "document": self._get_doc_name(doc, default=f"document_{idx}"),
                        "section": self._get_section(doc),
                        "text": text,
                        "source": "nlp_result_document_source",
                        "text_origin": "extracted_before_llm",
                    }
                )

        # Packs top-level
        for pack in self.IMPORTANT_PACKS:
            items = data.get(pack) or []
            passages.extend(self._load_items_from_pack(items, pack, "nlp_result_pack_source"))

        # Evidence pack fréquent dans ton pipeline
        for evidence_key in ["evidence_pack_for_ennodiagnostic", "evidence_pack_before_frascati", "qualified_pack_for_ennodiagnostic"]:
            pack_obj = data.get(evidence_key) or {}
            if isinstance(pack_obj, dict):
                for pack, items in pack_obj.items():
                    if isinstance(items, list):
                        passages.extend(self._load_items_from_pack(items, str(pack), f"nlp_{evidence_key}"))

        # sections structurées CIR/source
        sections = data.get("sections") or []
        if isinstance(sections, list):
            passages.extend(self._load_items_from_pack(sections, "sections", "nlp_sections_source"))

        return passages

    def _load_items_from_pack(self, items: Any, pack: str, source: str) -> List[Dict[str, Any]]:
        passages: List[Dict[str, Any]] = []
        if not isinstance(items, list):
            return passages

        for idx, item in enumerate(items):
            if isinstance(item, str):
                text = clean_text(item)
                doc = ""
                section = ""
            elif isinstance(item, dict):
                text = clean_text(self._extract_source_text_from_item(item))
                doc = self._get_doc_name(item)
                section = self._get_section(item)
            else:
                continue

            if not text:
                continue

            passage_id = ""
            if isinstance(item, dict):
                passage_id = str(item.get("id") or item.get("passage_id") or item.get("rag_chunk_id") or "")
            if not passage_id:
                passage_id = f"{pack}_{idx}_{text_hash(text[:300])}"

            passages.append(
                {
                    "passage_id": passage_id,
                    "pack": pack,
                    "role": self._pack_to_role(pack),
                    "document": doc,
                    "section": section,
                    "text": text,
                    "source": source,
                    "text_origin": "extracted_before_llm",
                }
            )
        return passages

    def _load_from_processed_dir(self, processed_dir: Path) -> List[Dict[str, Any]]:
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
                    text = clean_text(path.read_text(encoding="utf-8", errors="ignore"))
                    if text:
                        passages.append(
                            {
                                "passage_id": f"processed_txt_{text_hash(str(path))}",
                                "pack": "document_extrait",
                                "role": "document_extrait",
                                "document": path.name,
                                "section": "",
                                "text": text,
                                "source": "processed_txt_source",
                                "text_origin": "extracted_before_llm",
                            }
                        )
                else:
                    data = _read_json_safe(path, None)
                    passages.extend(self._extract_from_any_json(data, source_file=path.name))
            except Exception:
                continue

        return passages

    def _extract_from_any_json(self, data: Any, source_file: str) -> List[Dict[str, Any]]:
        passages: List[Dict[str, Any]] = []

        if isinstance(data, dict):
            text = self._extract_source_text_from_item(data)
            if text:
                passages.append(
                    {
                        "passage_id": f"processed_json_{text_hash(source_file + text[:100])}",
                        "pack": "document_extrait",
                        "role": "document_extrait",
                        "document": self._get_doc_name(data, default=source_file),
                        "section": self._get_section(data),
                        "text": text,
                        "source": "processed_json_source",
                        "text_origin": "extracted_before_llm",
                    }
                )

            for key, value in data.items():
                if key in self.GENERATED_TEXT_KEYS:
                    continue
                if isinstance(value, list):
                    passages.extend(self._load_items_from_pack(value, str(key), "processed_json_source"))

        elif isinstance(data, list):
            passages.extend(self._load_items_from_pack(data, "document_extrait", "processed_json_source"))

        return passages

    def _load_from_rag_chunks(self, rag_path: Path) -> List[Dict[str, Any]]:
        data = _read_json_safe(rag_path, [])
        if isinstance(data, dict):
            chunks = data.get("chunks") or data.get("items") or []
        elif isinstance(data, list):
            chunks = data
        else:
            chunks = []

        passages: List[Dict[str, Any]] = []

        for idx, ch in enumerate(chunks):
            if not isinstance(ch, dict):
                continue
            meta = ch.get("metadata") if isinstance(ch.get("metadata"), dict) else {}
            if self._is_generated_like(ch) or self._is_generated_like(meta):
                continue

            text = clean_text(ch.get("source_text") or ch.get("text") or "")
            if not text:
                continue

            passages.append(
                {
                    "passage_id": str(ch.get("id") or meta.get("rag_chunk_id") or f"rag_{idx}"),
                    "pack": str(meta.get("pack_key") or ""),
                    "role": str(meta.get("role") or self._pack_to_role(str(meta.get("pack_key") or ""))),
                    "document": str(meta.get("document") or ""),
                    "section": str(meta.get("section_title") or ""),
                    "text": text,
                    "source": "rag_chunks_source_fallback",
                    "text_origin": "extracted_before_llm_via_rag_chunk",
                }
            )

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

    def _dedupe_passages(self, passages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        out: List[Dict[str, Any]] = []
        for p in passages:
            text = clean_text(p.get("text", ""))
            if not text:
                continue
            key = text_hash(text[:1200].lower())
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
        return out


# ============================================================
# Heuristiques simples
# ============================================================

class AIHeuristicScorer:
    GENERIC_PATTERNS = [
        r"\bvise à\b", r"\ba pour objectif\b", r"\bs'inscrit dans\b", r"\bdans le cadre de\b",
        r"\bpermet de\b", r"\baméliorer\b", r"\boptimiser\b", r"\brévolutionner\b",
        r"\bmettre en place\b", r"\bsolution innovante\b", r"\bapproche innovante\b",
        r"\bgains? de productivité\b", r"\bqualité\b", r"\brobustesse\b", r"\bpertinence\b",
        r"\benjeux\b", r"\bstratégique\b",
    ]

    EVIDENCE_PATTERNS = [
        r"\b\d+(\,\d+|\.\d+)?\s?%\b",
        r"\b\d+\s?(ms|s|min|h|jours?|semaines?|mois|µm|μm|mg|ml|cm²|cm2)\b",
        r"\bp\s?<\s?0[,.]\d+\b", r"\bn\s?=\s?\d+\b",
        r"\bprototype\b", r"\btesté\b", r"\bmesuré\b", r"\bdosé\b",
        r"\brésultat\b", r"\bexpérience\b", r"\bessai\b", r"\btableau\b", r"\bfigure\b",
    ]

    def score(self, text: str) -> Dict[str, Any]:
        text = clean_text(text)
        lower = text.lower()
        reasons: List[str] = []
        score = 0.0

        if len(text) < 120:
            return {"heuristic_score": 0.0, "reasons": ["passage trop court pour une analyse heuristique fiable"]}

        sentences = [s.strip() for s in re.split(r"[.!?]", text) if len(s.strip()) > 20]
        if sentences:
            lengths = [len(s.split()) for s in sentences]
            avg_len = sum(lengths) / max(1, len(lengths))
            if avg_len > 24:
                score += 0.12
                reasons.append("phrases longues et très structurées")
            if len(sentences) >= 4:
                variance = sum((x - avg_len) ** 2 for x in lengths) / len(lengths)
                if math.sqrt(variance) < 8:
                    score += 0.10
                    reasons.append("longueur des phrases très régulière")

        generic_hits = sum(1 for pat in self.GENERIC_PATTERNS if re.search(pat, lower, flags=re.IGNORECASE))
        if generic_hits >= 4:
            score += 0.18
            reasons.append("plusieurs formulations génériques ou institutionnelles")
        elif generic_hits >= 2:
            score += 0.10
            reasons.append("quelques formulations génériques")

        words = re.findall(r"\b[a-zA-ZÀ-ÿ]{5,}\b", lower)
        if len(words) > 60:
            freq: Dict[str, int] = {}
            for w in words:
                freq[w] = freq.get(w, 0) + 1
            repeated = [w for w, c in freq.items() if c >= 4]
            if len(repeated) >= 4:
                score += 0.12
                reasons.append("répétition de plusieurs termes importants")

        evidence_hits = sum(1 for pat in self.EVIDENCE_PATTERNS if re.search(pat, text, flags=re.IGNORECASE))
        if evidence_hits == 0 and len(text) > 400:
            score += 0.18
            reasons.append("peu ou pas de preuves techniques concrètes détectées")
        elif evidence_hits <= 1 and len(text) > 700:
            score += 0.10
            reasons.append("peu d'éléments techniques vérifiables")

        connectors = ["ainsi", "cependant", "par ailleurs", "en outre", "de plus", "dans ce contexte", "afin de", "en particulier"]
        connector_hits = sum(1 for c in connectors if c in lower)
        if connector_hits >= 4:
            score += 0.10
            reasons.append("enchaînement rédactionnel très fluide et standardisé")

        score = max(0.0, min(1.0, score))
        if not reasons:
            reasons.append("aucun signal heuristique fort détecté")
        return {"heuristic_score": round(score, 4), "reasons": reasons}


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
        self.model_name = model_name or os.getenv("AI_DETECTOR_MODEL", "AICodexLab/answerdotai-ModernBERT-base-ai-detector")
        self.max_chars = max_chars or _int_env("AI_DETECTOR_MAX_CHARS", 2500)
        self.min_chars = min_chars or _int_env("AI_DETECTOR_MIN_CHARS", 120)
        self.threshold_medium = _float_env("AI_DETECTOR_THRESHOLD_MEDIUM", 0.45)
        self.threshold_high = _float_env("AI_DETECTOR_THRESHOLD_HIGH", 0.70)
        self.model_weight = model_weight or _float_env("AI_DETECTOR_MODEL_WEIGHT", 0.75)
        self.heuristic_weight = heuristic_weight or _float_env("AI_DETECTOR_HEURISTIC_WEIGHT", 0.25)
        self.device_name = os.getenv("AI_DETECTOR_DEVICE", "cpu").lower().strip()
        self.heuristic = AIHeuristicScorer()
        self.pipeline = None

    def load_model(self):
        if self.pipeline is not None:
            return self.pipeline

        import torch
        from transformers import pipeline

        if self.device_name == "cuda" and torch.cuda.is_available():
            device = 0
            device_label = "cuda"
        else:
            device = -1
            device_label = "cpu"

        self.pipeline = pipeline(
            task="text-classification",
            model=self.model_name,
            tokenizer=self.model_name,
            device=device,
            truncation=True,
        )

        print(f"✅ AI detector chargé sur {device_label}")
        return self.pipeline

    def _model_score_to_ai_score(self, outputs: Any) -> Tuple[float, str, float]:
        if isinstance(outputs, list) and outputs and isinstance(outputs[0], list):
            outputs = outputs[0]
        if isinstance(outputs, dict):
            outputs = [outputs]
        if not isinstance(outputs, list) or not outputs:
            return 0.0, "unknown", 0.0

        best = outputs[0]
        label = str(best.get("label", "unknown"))
        conf = float(best.get("score", 0.0))
        low = label.lower()

        if any(k in low for k in ["ai", "generated", "machine", "llm"]):
            return conf, label, conf
        if "human" in low or "real" in low:
            return 1.0 - conf, label, conf
        if low in ["label_1", "1"]:
            return conf, label, conf
        if low in ["label_0", "0"]:
            return 1.0 - conf, label, conf
        return conf, label, conf

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
                "ai_score": 0.0, "model_ai_score": 0.0, "heuristic_score": 0.0,
                "risk_level": "faible", "model_label": "too_short", "model_confidence": 0.0,
                "reasons": ["passage trop court"],
            }

        model = self.load_model()
        truncated = text[: self.max_chars]

        try:
            raw_outputs = model(truncated)
            model_ai_score, model_label, model_confidence = self._model_score_to_ai_score(raw_outputs)
        except Exception as e:
            print(f"⚠ Score modèle IA indisponible, heuristique utilisée seule : {e}")
            model_ai_score = 0.0
            model_label = "model_error"
            model_confidence = 0.0

        heuristic_result = self.heuristic.score(text)
        heuristic_score = float(heuristic_result["heuristic_score"])

        if model_label == "model_error":
            final_score = heuristic_score
        else:
            final_score = self.model_weight * model_ai_score + self.heuristic_weight * heuristic_score

        final_score = max(0.0, min(1.0, final_score))

        return {
            "ai_score": round(final_score, 4),
            "model_ai_score": round(model_ai_score, 4),
            "heuristic_score": round(heuristic_score, 4),
            "risk_level": self._risk_level(final_score),
            "model_label": model_label,
            "model_confidence": round(model_confidence, 4),
            "reasons": heuristic_result.get("reasons", []),
        }

    def analyze_passages(self, passages: List[Dict[str, Any]]) -> Dict[str, Any]:
        analyzed: List[Dict[str, Any]] = []

        for idx, passage in enumerate(passages):
            original_text = clean_text(passage.get("text", ""))
            if not original_text:
                continue

            for j, subtext in enumerate(split_long_text(original_text, max_chars=self.max_chars)):
                if len(subtext) < self.min_chars:
                    continue
                score_data = self.analyze_text(subtext)
                analyzed.append(
                    {
                        "passage_id": f"{passage.get('passage_id', idx)}_{j}",
                        "parent_passage_id": passage.get("passage_id", str(idx)),
                        "document": passage.get("document", ""),
                        "section": passage.get("section", ""),
                        "pack": passage.get("pack", ""),
                        "role": passage.get("role", ""),
                        "source": passage.get("source", ""),
                        "text_origin": passage.get("text_origin", "extracted_before_llm"),
                        "text": subtext,
                        **score_data,
                        "needs_human_validation": score_data["risk_level"] in ["moyen", "élevé"],
                    }
                )

        analyzed = sorted(analyzed, key=lambda x: x.get("ai_score", 0), reverse=True)
        global_score = self._global_score(analyzed)
        suspected = [p for p in analyzed if p.get("risk_level") in ["moyen", "élevé"]]

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "model_name": self.model_name,
            "device": self.device_name,
            "input_policy": "extracted_content_before_llm_only",
            "global_ai_score": round(global_score, 4),
            "global_ai_percentage": round(global_score * 100, 2),
            "risk_level": self._risk_level(global_score),
            "total_passages_analyzed": len(analyzed),
            "suspected_passages_count": len(suspected),
            "high_risk_passages_count": len([p for p in analyzed if p.get("risk_level") == "élevé"]),
            "medium_risk_passages_count": len([p for p in analyzed if p.get("risk_level") == "moyen"]),
            "low_risk_passages_count": len([p for p in analyzed if p.get("risk_level") == "faible"]),
            "passages": analyzed,
            "suspected_passages": suspected,
            "disclaimer": (
                "Ce score indique une suspicion de contenu généré ou reformulé par IA. "
                "Il porte uniquement sur les contenus extraits avant reformulation LLM. "
                "Il ne constitue pas une preuve certaine et doit être validé humainement."
            ),
        }

    def _global_score(self, analyzed: List[Dict[str, Any]]) -> float:
        if not analyzed:
            return 0.0
        total_weight = 0.0
        weighted_sum = 0.0
        for p in analyzed:
            text_len = len(p.get("text", ""))
            length_weight = min(3.0, max(1.0, text_len / 600))
            score = float(p.get("ai_score", 0.0))
            weight = length_weight * (1.0 + score)
            weighted_sum += score * weight
            total_weight += weight
        return weighted_sum / max(1e-6, total_weight)


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
    ):
        _load_project_env()
        self.organisme_input = organisme
        self.project_input = project
        self.year = str(year or os.getenv("ENNOSMART_PROJECT_YEAR", "") or "").strip()

        self.organisme = safe_name(organisme)
        self.project = safe_name(project)

        self.base_dir = Path(_str_env("ENNOSMART_BASE_DIR", r"C:\EnnoSmart"))

        self.loader = EnnoExtractedContentLoader(
            organisme=organisme,
            project=project,
            year=self.year,
            base_dir=self.base_dir,
            allow_rag_fallback=allow_rag_fallback,
        )

        self.project_root = self.loader.project_root
        self.year_root = self.loader.year_root
        self.diagnostics_dir = (self.year_root or self.project_root) / "diagnostics"
        self.diagnostics_dir.mkdir(parents=True, exist_ok=True)

        self.detector = ModernBERTAITextDetector()

    def run(self, save: bool = True) -> Dict[str, Any]:
        passages, loader_report = self.loader.load_passages()
        report = self.detector.analyze_passages(passages)

        result = {
            "organisme_id": self.organisme,
            "project_id": self.project,
            "year": self.year,
            "input_policy": "extracted_content_before_llm_only",
            "loader_report": loader_report,
            "ai_detection": report,
            "summary": {
                "average_ai_score": report.get("global_ai_score", 0.0),
                "average_ai_percentage": report.get("global_ai_percentage", 0.0),
                "risk_level": report.get("risk_level", "faible"),
                "passages_count": report.get("total_passages_analyzed", 0),
                "suspected_passages_count": report.get("suspected_passages_count", 0),
                "high_count": report.get("high_risk_passages_count", 0),
                "medium_count": report.get("medium_risk_passages_count", 0),
                "low_count": report.get("low_risk_passages_count", 0),
            },
        }

        if save:
            self.save_report(result)
        return result

    def save_report(self, result: Dict[str, Any]) -> Path:
        path = self.diagnostics_dir / "ai_detection_report.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


# ============================================================
# CLI test rapide
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="EnnoSmart AI Detection - extracted content only")
    parser.add_argument("--organisme", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--year", default="")
    parser.add_argument("--no-rag-fallback", action="store_true")
    args = parser.parse_args()

    service = EnnoAIDetectionService(
        organisme=args.organisme,
        project=args.project,
        year=args.year,
        allow_rag_fallback=not args.no_rag_fallback,
    )
    result = service.run(save=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
