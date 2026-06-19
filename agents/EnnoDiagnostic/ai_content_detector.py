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
# Helpers texte
# ============================================================

def clean_text(text: str) -> str:
    text = str(text or "")
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def safe_name(x: str) -> str:
    x = str(x or "").strip().lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’'", "aaaeeeeiioouuuc__")
    x = x.translate(tr)
    x = re.sub(r"[^a-z0-9_\-]+", "_", x)
    x = re.sub(r"_+", "_", x).strip("_")
    return x or "default"


def text_hash(text: str) -> str:
    return hashlib.sha1(clean_text(text).encode("utf-8", errors="ignore")).hexdigest()[:16]


def split_long_text(text: str, max_chars: int = 2500) -> List[str]:
    """
    Découpe un long texte en passages analysables.
    Cette découpe ne reformule jamais le texte.
    """
    text = clean_text(text)
    if len(text) <= max_chars:
        return [text] if text else []

    # Découpe naturelle par phrases.
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

    # Fallback si un texte ne contient pas de ponctuation.
    fixed_chunks: List[str] = []
    for ch in chunks:
        if len(ch) <= max_chars:
            fixed_chunks.append(ch)
        else:
            for i in range(0, len(ch), max_chars):
                part = ch[i : i + max_chars].strip()
                if part:
                    fixed_chunks.append(part)

    return fixed_chunks


# ============================================================
# Loader STRICT : contenu extrait uniquement, avant LLM
# ============================================================

class EnnoExtractedContentLoader:
    """
    Charge uniquement des passages issus du contenu extrait ou des passages NLP source.

    Objectif :
    - analyser le contenu client extrait directement ;
    - exclure toute reformulation LLM ;
    - exclure diagnostic_ennodiagnostic.json ;
    - exclure les champs summary/resume/reformulation/content générés.

    Priorité :
    1) documents extraits dans nlp_result.json si disponibles ;
    2) passages NLP avec texte source uniquement ;
    3) fallback optionnel vers rag/chunks.json, car les chunks sont normalement
       construits depuis les passages sources, pas depuis le diagnostic LLM.

    Le rapport final indique clairement la provenance de chaque passage.
    """

    # Champs considérés comme texte source.
    # On n'inclut PAS : reformulation, summary, resume, diagnostic, llm_output.
    SOURCE_TEXT_KEYS = [
        "raw_text",
        "original_text",
        "source_text",
        "extracted_text",
        "text",
        "passage",
        "passage_source",
    ]

    # Champs interdits car souvent générés/reformulés.
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

    # Packs NLP acceptés si leurs items contiennent un texte source.
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

    # Clés possibles contenant les documents complets extraits.
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
        base_dir: Optional[Path] = None,
        allow_rag_fallback: Optional[bool] = None,
    ):
        _load_project_env()

        self.organisme = safe_name(organisme)
        self.project = safe_name(project)

        self.base_dir = base_dir or Path(_str_env("ENNOSMART_BASE_DIR", r"C:\EnnoSmart"))
        self.project_root = (
            self.base_dir
            / "storage"
            / "organismes"
            / self.organisme
            / "projects"
            / self.project
        )

        self.nlp_path = self.project_root / "nlp" / "nlp_result.json"
        self.rag_chunks_path = self.project_root / "rag" / "chunks.json"
        self.processed_dir = self.project_root / "documents" / "processed"

        if allow_rag_fallback is None:
            allow_rag_fallback = os.getenv("AI_DETECTOR_ALLOW_RAG_FALLBACK", "1") == "1"
        self.allow_rag_fallback = bool(allow_rag_fallback)

        self.loader_report: Dict[str, Any] = {
            "input_policy": "extracted_content_before_llm_only",
            "project_root": str(self.project_root),
            "nlp_path": str(self.nlp_path),
            "rag_chunks_path": str(self.rag_chunks_path),
            "processed_dir": str(self.processed_dir),
            "allow_rag_fallback": self.allow_rag_fallback,
            "used_sources": [],
            "excluded_generated_fields": self.GENERATED_TEXT_KEYS,
            "notes": [
                "Le loader ne lit jamais diagnostic_ennodiagnostic.json.",
                "Les champs reformulation/summary/resume/diagnostic/llm_output sont ignorés.",
                "Le score IA porte sur les textes sources extraits ou les passages NLP source.",
            ],
        }

    def load_passages(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        passages: List[Dict[str, Any]] = []

        # 1) NLP result : documents complets + packs source
        if self.nlp_path.exists():
            nlp_data = self._load_json(self.nlp_path)
            nlp_passages = self._load_from_nlp(nlp_data)
            passages.extend(nlp_passages)
            self.loader_report["used_sources"].append(
                {
                    "source": "nlp_result",
                    "path": str(self.nlp_path),
                    "passages_loaded": len(nlp_passages),
                }
            )

        # 2) Processed JSON/TXT fallback, si le NLP ne donne rien
        if not passages:
            processed_passages = self._load_from_processed_dir()
            passages.extend(processed_passages)
            self.loader_report["used_sources"].append(
                {
                    "source": "documents_processed",
                    "path": str(self.processed_dir),
                    "passages_loaded": len(processed_passages),
                }
            )

        # 3) RAG chunks fallback seulement si rien d'autre.
        # Les chunks ne doivent pas venir du diagnostic LLM.
        if not passages and self.allow_rag_fallback and self.rag_chunks_path.exists():
            rag_passages = self._load_from_rag_chunks()
            passages.extend(rag_passages)
            self.loader_report["used_sources"].append(
                {
                    "source": "rag_chunks_source_fallback",
                    "path": str(self.rag_chunks_path),
                    "passages_loaded": len(rag_passages),
                    "warning": "fallback accepté uniquement si les chunks sont construits depuis les sources extraites",
                }
            )

        passages = self._dedupe_passages(passages)

        self.loader_report["total_passages_after_dedupe"] = len(passages)
        self.loader_report["documents_covered"] = sorted(
            list({str(p.get("document", "")) for p in passages if p.get("document")})
        )
        self.loader_report["documents_covered_count"] = len(self.loader_report["documents_covered"])

        return passages, self.loader_report

    def _load_json(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def _is_generated_like(self, item: Dict[str, Any]) -> bool:
        """
        Refuse un item si ses métadonnées indiquent qu'il s'agit de texte généré.
        """
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

        bad_markers = [
            "llm",
            "generated",
            "reformulation",
            "diagnostic",
            "gemini",
            "openrouter",
            "chatgpt",
            "synthetic",
        ]

        return any(m in joined for m in bad_markers)

    def _extract_source_text_from_item(self, item: Any) -> str:
        """
        Extrait uniquement un texte source.
        Ne lit jamais les champs de reformulation/synthèse.
        """
        if isinstance(item, str):
            return clean_text(item)

        if not isinstance(item, dict):
            return ""

        if self._is_generated_like(item):
            return ""

        # Ne pas lire les champs générés.
        for bad_key in self.GENERATED_TEXT_KEYS:
            # Leur présence ne bloque pas tout l'item, mais on ne les lit jamais.
            _ = item.get(bad_key)

        parts: List[str] = []

        for key in self.SOURCE_TEXT_KEYS:
            value = item.get(key)
            if isinstance(value, str):
                txt = clean_text(value)
                if len(txt) > 20:
                    parts.append(txt)

        # Sources/preuves/supporting_passages peuvent contenir des textes originaux.
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
        return str(item.get("section_title") or item.get("section") or item.get("title") or "")

    def _load_from_nlp(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        passages: List[Dict[str, Any]] = []

        # A) documents complets extraits dans nlp_result
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

        # B) packs NLP source : seulement les passages originaux, pas les résumés
        for pack in self.IMPORTANT_PACKS:
            items = data.get(pack) or []
            if not isinstance(items, list):
                continue

            for idx, item in enumerate(items):
                if not isinstance(item, dict):
                    continue

                text = self._extract_source_text_from_item(item)
                text = clean_text(text)

                if not text:
                    continue

                passage_id = (
                    item.get("id")
                    or item.get("passage_id")
                    or item.get("rag_chunk_id")
                    or f"{pack}_{idx}"
                )

                passages.append(
                    {
                        "passage_id": str(passage_id),
                        "pack": pack,
                        "role": self._pack_to_role(pack),
                        "document": self._get_doc_name(item),
                        "section": self._get_section(item),
                        "text": text,
                        "source": "nlp_result_pack_source",
                        "text_origin": "extracted_before_llm",
                    }
                )

        # C) multi-doc summaries : on ne les lit pas comme texte à scorer si ce sont des summaries.
        # On les ignore volontairement, car tu veux scorer le contenu extrait, pas les fiches/synthèses.

        return passages

    def _load_from_processed_dir(self) -> List[Dict[str, Any]]:
        passages: List[Dict[str, Any]] = []
        if not self.processed_dir.exists():
            return passages

        allowed_ext = {".json", ".txt"}

        for path in sorted(self.processed_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in allowed_ext:
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

                elif path.suffix.lower() == ".json":
                    data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
                    passages.extend(self._extract_from_any_json(data, source_file=path.name))

            except Exception:
                continue

        return passages

    def _extract_from_any_json(self, data: Any, source_file: str) -> List[Dict[str, Any]]:
        passages: List[Dict[str, Any]] = []

        if isinstance(data, dict):
            # Un document complet possible.
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

            # Explorer les listes de documents/passages.
            for key, value in data.items():
                if key in self.GENERATED_TEXT_KEYS:
                    continue
                if isinstance(value, list):
                    for idx, item in enumerate(value):
                        if isinstance(item, dict):
                            subtext = self._extract_source_text_from_item(item)
                            if subtext:
                                passages.append(
                                    {
                                        "passage_id": f"processed_json_{key}_{idx}_{text_hash(subtext[:300])}",
                                        "pack": key,
                                        "role": self._pack_to_role(key),
                                        "document": self._get_doc_name(item, default=source_file),
                                        "section": self._get_section(item),
                                        "text": subtext,
                                        "source": "processed_json_source",
                                        "text_origin": "extracted_before_llm",
                                    }
                                )

        elif isinstance(data, list):
            for idx, item in enumerate(data):
                if isinstance(item, dict):
                    text = self._extract_source_text_from_item(item)
                    if text:
                        passages.append(
                            {
                                "passage_id": f"processed_json_list_{idx}_{text_hash(text[:300])}",
                                "pack": "document_extrait",
                                "role": "document_extrait",
                                "document": self._get_doc_name(item, default=source_file),
                                "section": self._get_section(item),
                                "text": text,
                                "source": "processed_json_source",
                                "text_origin": "extracted_before_llm",
                            }
                        )

        return passages

    def _load_from_rag_chunks(self) -> List[Dict[str, Any]]:
        data = self._load_json(self.rag_chunks_path)

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

            meta = ch.get("metadata") or {}

            # Refuser chunks marqués comme génération LLM.
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
    """
    Score heuristique simple :
    - style générique,
    - phrases trop standardisées,
    - répétitions,
    - manque de preuves concrètes,
    - vocabulaire marketing / flou.

    Attention : ce score est indicatif, jamais une preuve.
    """

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
                "reasons": ["passage trop court pour une analyse heuristique fiable"],
            }

        sentences = [s.strip() for s in re.split(r"[.!?]", text) if len(s.strip()) > 20]
        if sentences:
            lengths = [len(s.split()) for s in sentences]
            avg_len = sum(lengths) / max(1, len(lengths))

            if avg_len > 24:
                score += 0.12
                reasons.append("phrases longues et très structurées")

            if len(sentences) >= 4:
                variance = sum((x - avg_len) ** 2 for x in lengths) / len(lengths)
                std = math.sqrt(variance)
                if std < 8:
                    score += 0.10
                    reasons.append("longueur des phrases très régulière")

        generic_hits = 0
        for pat in self.GENERIC_PATTERNS:
            if re.search(pat, lower, flags=re.IGNORECASE):
                generic_hits += 1

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

        evidence_hits = 0
        for pat in self.EVIDENCE_PATTERNS:
            if re.search(pat, text, flags=re.IGNORECASE):
                evidence_hits += 1

        if evidence_hits == 0 and len(text) > 400:
            score += 0.18
            reasons.append("peu ou pas de preuves techniques concrètes détectées")
        elif evidence_hits <= 1 and len(text) > 700:
            score += 0.10
            reasons.append("peu d'éléments techniques vérifiables")

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

        connector_hits = sum(1 for c in connectors if c in lower)
        if connector_hits >= 4:
            score += 0.10
            reasons.append("enchaînement rédactionnel très fluide et standardisé")

        score = max(0.0, min(1.0, score))

        if not reasons:
            reasons.append("aucun signal heuristique fort détecté")

        return {
            "heuristic_score": round(score, 4),
            "reasons": reasons,
        }


# ============================================================
# ModernBERT Detector
# ============================================================

class ModernBERTAITextDetector:
    """
    Utilise AICodexLab/answerdotai-ModernBERT-base-ai-detector.
    """

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

        self.max_chars = max_chars or _int_env("AI_DETECTOR_MAX_CHARS", 2500)
        self.min_chars = min_chars or _int_env("AI_DETECTOR_MIN_CHARS", 120)

        self.threshold_medium = _float_env("AI_DETECTOR_THRESHOLD_MEDIUM", 0.45)
        self.threshold_high = _float_env("AI_DETECTOR_THRESHOLD_HIGH", 0.70)

        self.model_weight = model_weight or _float_env("AI_DETECTOR_MODEL_WEIGHT", 0.75)
        self.heuristic_weight = heuristic_weight or _float_env("AI_DETECTOR_HEURISTIC_WEIGHT", 0.25)

        self.heuristic = AIHeuristicScorer()
        self.pipeline = None

    def load_model(self):
        if self.pipeline is not None:
            return self.pipeline

        import torch
        from transformers import pipeline

        device = 0 if torch.cuda.is_available() else -1

        self.pipeline = pipeline(
            task="text-classification",
            model=self.model_name,
            tokenizer=self.model_name,
            device=device,
            truncation=True,
        )

        return self.pipeline

    def _model_score_to_ai_score(self, outputs: Any) -> Tuple[float, str, float]:
        """
        Convertit la sortie HF en score IA entre 0 et 1.
        Compatible avec labels variés.
        """
        if isinstance(outputs, list) and outputs and isinstance(outputs[0], list):
            outputs = outputs[0]

        if isinstance(outputs, dict):
            outputs = [outputs]

        if not isinstance(outputs, list) or not outputs:
            return 0.0, "unknown", 0.0

        best = outputs[0]
        label = str(best.get("label", "unknown"))
        conf = float(best.get("score", 0.0))

        label_lower = label.lower()

        if any(k in label_lower for k in ["ai", "generated", "machine", "synthetic", "llm"]):
            return conf, label, conf

        if "human" in label_lower or "real" in label_lower:
            return 1.0 - conf, label, conf

        if label_lower in ["label_1", "1"]:
            return conf, label, conf

        if label_lower in ["label_0", "0"]:
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
                "ai_score": 0.0,
                "model_ai_score": 0.0,
                "heuristic_score": 0.0,
                "risk_level": "faible",
                "model_label": "too_short",
                "model_confidence": 0.0,
                "reasons": ["passage trop court"],
            }

        model = self.load_model()

        truncated_text = text[: self.max_chars]
        raw_outputs = model(truncated_text)

        model_ai_score, model_label, model_confidence = self._model_score_to_ai_score(raw_outputs)

        heuristic_result = self.heuristic.score(text)
        heuristic_score = float(heuristic_result["heuristic_score"])

        final_score = (
            self.model_weight * model_ai_score
            + self.heuristic_weight * heuristic_score
        )

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

            subtexts = split_long_text(original_text, max_chars=self.max_chars)

            for j, subtext in enumerate(subtexts):
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
        risk_level = self._risk_level(global_score)

        suspected = [
            p for p in analyzed
            if p.get("risk_level") in ["moyen", "élevé"]
        ]

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "model_name": self.model_name,
            "input_policy": "extracted_content_before_llm_only",
            "global_ai_score": round(global_score, 4),
            "global_ai_percentage": round(global_score * 100, 2),
            "risk_level": risk_level,
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
            suspicion_weight = 1.0 + score

            weight = length_weight * suspicion_weight
            weighted_sum += score * weight
            total_weight += weight

        return weighted_sum / max(1e-6, total_weight)


# ============================================================
# Service projet complet
# ============================================================

class EnnoAIDetectionService:
    """
    Service complet :
    - charge uniquement les passages extraits avant LLM,
    - applique ModernBERT + heuristiques,
    - sauvegarde le rapport.

    Le service ne lit pas diagnostic_ennodiagnostic.json.
    """

    def __init__(
        self,
        organisme: str,
        project: str,
        allow_rag_fallback: Optional[bool] = None,
    ):
        self.organisme = safe_name(organisme)
        self.project = safe_name(project)

        self.base_dir = Path(_str_env("ENNOSMART_BASE_DIR", r"C:\EnnoSmart"))
        self.project_root = (
            self.base_dir
            / "storage"
            / "organismes"
            / self.organisme
            / "projects"
            / self.project
        )

        self.diagnostics_dir = self.project_root / "diagnostics"
        self.diagnostics_dir.mkdir(parents=True, exist_ok=True)

        self.loader = EnnoExtractedContentLoader(
            self.organisme,
            self.project,
            base_dir=self.base_dir,
            allow_rag_fallback=allow_rag_fallback,
        )
        self.detector = ModernBERTAITextDetector()

    def run(self, save: bool = True) -> Dict[str, Any]:
        passages, loader_report = self.loader.load_passages()

        report = self.detector.analyze_passages(passages)

        result = {
            "organisme_id": self.organisme,
            "project_id": self.project,
            "input_policy": "extracted_content_before_llm_only",
            "loader_report": loader_report,
            "ai_detection": report,
        }

        if save:
            self.save_report(result)

        return result

    def save_report(self, result: Dict[str, Any]) -> Path:
        path = self.diagnostics_dir / "ai_detection_report.json"
        path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path


# ============================================================
# CLI test rapide
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="EnnoSmart AI Detection - extracted content only")
    parser.add_argument("--organisme", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--no-rag-fallback", action="store_true")
    args = parser.parse_args()

    service = EnnoAIDetectionService(
        organisme=args.organisme,
        project=args.project,
        allow_rag_fallback=not args.no_rag_fallback,
    )
    result = service.run(save=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
