# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import math
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


def load_env() -> None:
    if load_dotenv is None:
        return
    for p in [Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"]:
        if p.exists():
            load_dotenv(p, override=True)


def env_str(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def env_int(name: str, default: int) -> int:
    try:
        return int(env_str(name, str(default)))
    except Exception:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(env_str(name, str(default)))
    except Exception:
        return default


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_text(text: Any) -> str:
    text = str(text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate(text: str, max_chars: int) -> str:
    text = clean_text(text)
    return text[:max_chars].rstrip() if len(text) > max_chars else text


PACK_KEYS = [
    "objectifs_locaux",
    "verrous_rnd_locaux",
    "methodes_locales",
    "resultats_locaux",
    "limites_locales",
    "contributions_locales",
    "etat_art_local",
    "parametres_locaux",
]

ROLE_LABELS = {
    "objectifs_locaux": "objectif",
    "verrous_rnd_locaux": "verrou",
    "methodes_locales": "methode",
    "resultats_locaux": "resultat",
    "limites_locales": "limite",
    "contributions_locales": "contribution",
    "etat_art_local": "etat_art",
    "parametres_locaux": "parametre",
}


def get_pack(nlp_result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(nlp_result, dict):
        return {}

    fg = nlp_result.get("frascati_guard") or {}
    if isinstance(fg, dict):
        pack = fg.get("qualified_pack_for_ennodiagnostic")
        if isinstance(pack, dict) and pack:
            return pack

    for key in [
        "multi_document_evidence_pack_for_ennodiagnostic",
        "merged_evidence_pack_for_ennodiagnostic",
        "evidence_pack_for_ennodiagnostic",
        "evidence_pack_before_frascati",
    ]:
        pack = nlp_result.get(key)
        if isinstance(pack, dict) and pack:
            return pack

    return {}


def extract_passages_from_nlp(nlp_result: Dict[str, Any], min_chars: int, max_chars: int) -> List[Dict[str, Any]]:
    pack = get_pack(nlp_result)
    out = []
    seen = set()

    for pack_key in PACK_KEYS:
        role = ROLE_LABELS.get(pack_key, pack_key)
        for idx, item in enumerate(pack.get(pack_key) or []):
            if not isinstance(item, dict):
                continue

            text = clean_text(item.get("text") or item.get("source_text") or "")
            if len(text) < min_chars:
                continue

            text = truncate(text, max_chars)
            doc = str(item.get("document") or "")
            key = (doc, role, text[:250])

            if key in seen:
                continue
            seen.add(key)

            out.append({
                "passage_id": str(item.get("passage_id") or f"{pack_key}_{idx}"),
                "role": role,
                "pack_key": pack_key,
                "document": doc,
                "source_path": str(item.get("source_path") or ""),
                "section_title": str(item.get("section_title") or ""),
                "text": text,
                "source": "nlp_main_item",
            })

    return out


def _split_fulltext_with_offsets(
    text: str,
    *,
    min_chars: int,
    max_chars: int,
) -> List[Dict[str, Any]]:
    """Découpe le texte complet sans perdre les offsets dans le document."""
    source = clean_text(text)
    if not source:
        return []

    max_chars = max(200, int(max_chars or 2500))
    min_chars = max(1, int(min_chars or 120))
    chunks: List[Dict[str, Any]] = []
    start = 0

    while start < len(source):
        end = min(len(source), start + max_chars)
        if end < len(source):
            floor = start + int(max_chars * 0.55)
            candidates = [
                source.rfind("\n\n", floor, end),
                source.rfind("\n", floor, end),
                source.rfind(". ", floor, end),
                source.rfind("; ", floor, end),
            ]
            boundary = max(candidates)
            if boundary >= floor:
                end = boundary + (2 if source[boundary:boundary + 2] in {". ", "; "} else 1)

        raw_chunk = source[start:end]
        leading = len(raw_chunk) - len(raw_chunk.lstrip())
        chunk_text = raw_chunk.strip()
        chunk_start = start + leading
        chunk_end = chunk_start + len(chunk_text)

        if len(chunk_text) >= min_chars:
            chunks.append({
                "text": chunk_text,
                "char_start": chunk_start,
                "char_end": chunk_end,
            })

        if end <= start:
            break
        start = end

    return chunks


def extract_passages_from_fulltext_corpus(
    corpus_dir: str | Path,
    *,
    min_chars: int,
    max_chars: int,
) -> List[Dict[str, Any]]:
    """
    Charge uniquement le corpus fulltext V2.

    Aucun ``nlp_result.json``, chunk RAG ou texte généré par LLM n'est lu ici.
    Chaque passage conserve le document, le chemin source et ses offsets.
    """
    corpus_dir = Path(corpus_dir)
    if not corpus_dir.is_dir():
        raise RuntimeError(
            f"Corpus fulltext introuvable pour la détection IA : {corpus_dir}"
        )

    passages: List[Dict[str, Any]] = []
    seen = set()

    for path in sorted(corpus_dir.glob("*.json")):
        if path.name.lower() in {"manifest.json", "nlp_result.json"}:
            continue

        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue

        text = clean_text(payload.get("text"))
        if not text:
            continue

        document = str(
            payload.get("document")
            or payload.get("file_name")
            or path.stem
        ).strip()
        document_id = str(
            payload.get("document_id")
            or payload.get("text_sha256")
            or path.stem
        ).strip()
        source_path = str(payload.get("source_path") or "").strip()

        for index, chunk in enumerate(
            _split_fulltext_with_offsets(
                text,
                min_chars=min_chars,
                max_chars=max_chars,
            )
        ):
            chunk_text = str(chunk.get("text") or "")
            dedupe_key = (document_id, chunk.get("char_start"), chunk_text[:250])
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            passages.append({
                "passage_id": f"fulltext_{document_id}_{index}",
                "original_passage_id": f"fulltext_{document_id}",
                "role": "document_fulltext",
                "pack_key": "fulltext_rag_v1",
                "document_id": document_id,
                "document": document,
                "document_name": document,
                "filename": document,
                "source_path": source_path,
                "char_start": chunk.get("char_start"),
                "char_end": chunk.get("char_end"),
                "text": chunk_text,
                "source_text_original": chunk_text,
                "text_excerpt": chunk_text,
                "excerpt": chunk_text,
                "source": "fulltext_corpus_v1",
                "text_origin": "extracted_before_llm",
                "metadata": {
                    "schema_version": payload.get("schema_version"),
                    "extension": payload.get("extension"),
                    "loader": payload.get("loader"),
                    "document_type": payload.get("document_type"),
                    "content_origin": payload.get("content_origin"),
                    "fulltext_corpus_path": str(path),
                },
            })

    if not passages:
        raise RuntimeError(
            f"Le corpus fulltext ne contient aucun passage exploitable : {corpus_dir}"
        )

    return passages


GENERIC_AI_PHRASES = [
    "il est important de noter",
    "dans le cadre de",
    "cette approche permet",
    "de manière significative",
    "afin de garantir",
    "il convient de",
    "en conclusion",
    "par conséquent",
    "de plus",
    "en outre",
    "dans cette perspective",
    "les résultats montrent que",
    "il est nécessaire de",
    "une solution robuste",
    "une approche innovante",
]

TECH_WORDS = [
    "bar", "kg", "mm", "°c", "hz", "db", "rpm", "m3/h", "nm",
    "essai", "test", "mesure", "prototype", "pression", "débit",
    "température", "segment", "carter", "piston", "contrepoids",
]


def heuristic_ai_score(text: str) -> Dict[str, Any]:
    t = clean_text(text)
    low = t.lower()
    if not t:
        return {"score": 0.0, "reasons": ["texte vide"]}

    reasons = []
    score = 0.0
    sentences = [s.strip() for s in re.split(r"[.!?]\s+", t) if len(s.strip()) > 10]
    words = re.findall(r"\b[\wÀ-ÿ'-]+\b", low)

    if len(sentences) >= 3:
        lengths = [len(s) for s in sentences]
        avg = sum(lengths) / max(1, len(lengths))
        std = math.sqrt(sum((x - avg) ** 2 for x in lengths) / max(1, len(lengths)))
        if avg > 90 and std < 45:
            score += 0.18
            reasons.append("phrases longues et régulières")

    generic_hits = [p for p in GENERIC_AI_PHRASES if p in low]
    if generic_hits:
        score += min(0.30, 0.08 * len(generic_hits))
        reasons.append("formulations génériques : " + ", ".join(generic_hits[:4]))

    if len(words) > 80:
        unique_ratio = len(set(words)) / max(1, len(words))
        if unique_ratio < 0.48:
            score += 0.15
            reasons.append("répétition lexicale élevée")

    digits = len(re.findall(r"\d", t))
    tech_hits = [w for w in TECH_WORDS if w in low]
    if len(t) > 500 and digits < 4 and len(tech_hits) < 2:
        score += 0.18
        reasons.append("peu de détails techniques ou chiffrés")

    if re.search(r"(^|\n)\s*(objectif|méthode|résultat|conclusion|contexte)\s*:", low):
        score += 0.08
        reasons.append("structure rédactionnelle très formelle")

    score = max(0.0, min(1.0, score))
    if not reasons:
        reasons.append("aucun signal heuristique fort")

    return {"score": round(score, 4), "reasons": reasons}


_MODEL_CACHE = None
_TOKENIZER_CACHE = None


def load_detector_model(model_name: str):
    global _MODEL_CACHE, _TOKENIZER_CACHE

    if _MODEL_CACHE is not None and _TOKENIZER_CACHE is not None:
        return _TOKENIZER_CACHE, _MODEL_CACHE

    try:
        from transformers import AutoTokenizer, AutoModelForSequenceClassification

        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForSequenceClassification.from_pretrained(model_name)
        model.eval()

        _TOKENIZER_CACHE = tokenizer
        _MODEL_CACHE = model
        return tokenizer, model

    except Exception:
        return None, None


def model_ai_score(text: str, model_name: str) -> Dict[str, Any]:
    tokenizer, model = load_detector_model(model_name)

    if tokenizer is None or model is None:
        return {
            "score": None,
            "label": "model_unavailable",
            "error": f"Impossible de charger le modèle {model_name}",
        }

    try:
        import torch
        import torch.nn.functional as F

        inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)

        with torch.no_grad():
            outputs = model(**inputs)

        probs = F.softmax(outputs.logits, dim=-1)[0].detach().cpu().tolist()
        id2label = getattr(model.config, "id2label", {}) or {}

        ai_idx = None
        for idx, label in id2label.items():
            lab = str(label).lower()
            if any(k in lab for k in ["ai", "generated", "machine", "synthetic", "chatgpt"]):
                ai_idx = int(idx)
                break

        if ai_idx is None:
            ai_idx = 1 if len(probs) > 1 else 0

        score = float(probs[ai_idx])

        return {
            "score": round(score, 4),
            "label": str(id2label.get(ai_idx, f"class_{ai_idx}")),
            "probs": [round(float(x), 4) for x in probs],
            "id2label": {str(k): str(v) for k, v in id2label.items()},
        }

    except Exception as e:
        return {"score": None, "label": "model_error", "error": str(e)}


def risk_from_score(score: float, medium: float, high: float) -> str:
    if score >= high:
        return "high"
    if score >= medium:
        return "medium"
    return "low"


def risk_label_fr(risk: str) -> str:
    return {
        "high": "élevé",
        "medium": "moyen",
        "low": "faible",
    }.get(str(risk or "").lower(), "inconnu")


def analyze_passage(
    passage: Dict[str, Any],
    model_name: str,
    model_weight: float,
    heuristic_weight: float,
    medium_threshold: float,
    high_threshold: float,
) -> Dict[str, Any]:
    text = clean_text(passage.get("text"))
    heur = heuristic_ai_score(text)
    mod = model_ai_score(text, model_name)

    h_score = float(heur.get("score") or 0.0)
    m_score = mod.get("score")

    if m_score is None:
        final_score = h_score
        mode = "heuristic_only"
    else:
        final_score = (float(m_score) * model_weight) + (h_score * heuristic_weight)
        mode = "model_plus_heuristic"

    final_score = max(0.0, min(1.0, final_score))
    risk = risk_from_score(final_score, medium_threshold, high_threshold)

    return {
        **passage,
        "ai_score": round(final_score, 4),
        "ai_risk": risk,
        "risk_level": risk_label_fr(risk),
        "needs_human_validation": risk in {"medium", "high"},
        "scoring_mode": mode,
        "model": mod,
        "heuristic": heur,
    }


def summarize_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not results:
        return {
            "passages_count": 0,
            "average_ai_score": None,
            "average_ai_percentage": None,
            "risk_level": "inconnu",
            "suspected_passages_count": 0,
            "traceable_suspected_passages_count": 0,
            "high_count": 0,
            "medium_count": 0,
            "low_count": 0,
            "documents": [],
        }

    scores = [float(x.get("ai_score") or 0.0) for x in results]
    avg = sum(scores) / len(scores)

    high_count = sum(1 for x in results if x.get("ai_risk") == "high")
    medium_count = sum(1 for x in results if x.get("ai_risk") == "medium")
    low_count = sum(1 for x in results if x.get("ai_risk") == "low")

    risk = "high" if high_count else ("medium" if medium_count else "low")
    docs = sorted(set(str(x.get("document") or "") for x in results if x.get("document")))
    suspected = [x for x in results if x.get("ai_risk") in {"medium", "high"}]
    traceable_suspected = [
        x
        for x in suspected
        if x.get("document_id") or x.get("document") or x.get("source_path")
    ]

    return {
        "passages_count": len(results),
        "average_ai_score": round(avg, 4),
        "average_ai_percentage": round(avg * 100, 2),
        "risk_level": risk_label_fr(risk),
        "suspected_passages_count": len(suspected),
        "traceable_suspected_passages_count": len(traceable_suspected),
        "high_count": high_count,
        "medium_count": medium_count,
        "low_count": low_count,
        "documents": docs,
    }


def run_ai_detection_on_nlp_result(nlp_result_path: str | Path, output_path: str | Path) -> Dict[str, Any]:
    load_env()

    nlp_result_path = Path(nlp_result_path)
    output_path = Path(output_path)

    model_name = env_str("AI_DETECTOR_MODEL", "AICodexLab/answerdotai-ModernBERT-base-ai-detector")
    max_chars = env_int("AI_DETECTOR_MAX_CHARS", 2500)
    min_chars = env_int("AI_DETECTOR_MIN_CHARS", 120)
    medium_threshold = env_float("AI_DETECTOR_THRESHOLD_MEDIUM", 0.45)
    high_threshold = env_float("AI_DETECTOR_THRESHOLD_HIGH", 0.70)
    model_weight = env_float("AI_DETECTOR_MODEL_WEIGHT", 0.75)
    heuristic_weight = env_float("AI_DETECTOR_HEURISTIC_WEIGHT", 0.25)

    nlp_result = read_json(nlp_result_path, {})
    passages = extract_passages_from_nlp(nlp_result, min_chars=min_chars, max_chars=max_chars)

    results = [
        analyze_passage(
            p,
            model_name=model_name,
            model_weight=model_weight,
            heuristic_weight=heuristic_weight,
            medium_threshold=medium_threshold,
            high_threshold=high_threshold,
        )
        for p in passages
    ]

    results = sorted(results, key=lambda x: float(x.get("ai_score") or 0), reverse=True)

    report = {
        "ok": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "nlp_result_main_items_only",
        "important_note": (
            "La détection IA est appliquée aux passages extraits des documents bruts via le NLP. "
            "Elle n'analyse pas la synthèse LLM générée par EnnoDiagnostic."
        ),
        "config": {
            "model_name": model_name,
            "max_chars": max_chars,
            "min_chars": min_chars,
            "medium_threshold": medium_threshold,
            "high_threshold": high_threshold,
            "model_weight": model_weight,
            "heuristic_weight": heuristic_weight,
        },
        "summary": summarize_results(results),
        "passages": results,
    }

    write_json(output_path, report)
    return report


def run_ai_detection_on_fulltext_corpus(
    corpus_dir: str | Path,
    output_path: str | Path | None = None,
) -> Dict[str, Any]:
    """Exécute la détection IA sur le texte complet extrait par le pipeline V2."""
    load_env()

    corpus_dir = Path(corpus_dir)
    model_name = env_str(
        "AI_DETECTOR_MODEL",
        "AICodexLab/answerdotai-ModernBERT-base-ai-detector",
    )
    max_chars = env_int("AI_DETECTOR_MAX_CHARS", 2500)
    min_chars = env_int("AI_DETECTOR_MIN_CHARS", 120)
    medium_threshold = env_float("AI_DETECTOR_THRESHOLD_MEDIUM", 0.45)
    high_threshold = env_float("AI_DETECTOR_THRESHOLD_HIGH", 0.70)
    model_weight = env_float("AI_DETECTOR_MODEL_WEIGHT", 0.75)
    heuristic_weight = env_float("AI_DETECTOR_HEURISTIC_WEIGHT", 0.25)

    passages = extract_passages_from_fulltext_corpus(
        corpus_dir,
        min_chars=min_chars,
        max_chars=max_chars,
    )
    results = [
        analyze_passage(
            passage,
            model_name=model_name,
            model_weight=model_weight,
            heuristic_weight=heuristic_weight,
            medium_threshold=medium_threshold,
            high_threshold=high_threshold,
        )
        for passage in passages
    ]
    results = sorted(
        results,
        key=lambda item: float(item.get("ai_score") or 0),
        reverse=True,
    )
    summary = summarize_results(results)
    suspected = [
        item
        for item in results
        if item.get("ai_risk") in {"medium", "high"}
    ]

    ai_detection = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model_name": model_name,
        "input_policy": "fulltext_corpus_before_llm_only",
        "global_ai_score": summary.get("average_ai_score"),
        "global_ai_percentage": summary.get("average_ai_percentage"),
        "risk_level": summary.get("risk_level"),
        "total_passages_analyzed": summary.get("passages_count"),
        "suspected_passages_count": summary.get("suspected_passages_count"),
        "traceable_suspected_passages_count": summary.get(
            "traceable_suspected_passages_count"
        ),
        "high_risk_passages_count": summary.get("high_count"),
        "medium_risk_passages_count": summary.get("medium_count"),
        "low_risk_passages_count": summary.get("low_count"),
        "documents": summary.get("documents") or [],
        "passages": results,
        "suspected_passages": suspected,
        "disclaimer": (
            "Ce score est indicatif. Il analyse uniquement le texte complet "
            "extrait avant toute reformulation par EnnoDiagnostic V2."
        ),
    }
    report = {
        "ok": True,
        "generated_at": ai_detection["generated_at"],
        "source": "fulltext_rag_v1",
        "input_policy": "fulltext_corpus_before_llm_only",
        "loader_report": {
            "source": "fulltext_rag_v1",
            "corpus_dir": str(corpus_dir),
            "nlp_result_used": False,
            "rag_chunks_used": False,
            "passages_loaded": len(passages),
            "documents_covered": summary.get("documents") or [],
        },
        "important_note": (
            "La détection IA lit directement le corpus fulltext V2. "
            "Elle n'utilise ni nlp_result.json ni une synthèse générée par LLM."
        ),
        "config": {
            "model_name": model_name,
            "max_chars": max_chars,
            "min_chars": min_chars,
            "medium_threshold": medium_threshold,
            "high_threshold": high_threshold,
            "model_weight": model_weight,
            "heuristic_weight": heuristic_weight,
        },
        "summary": summary,
        "passages": results,
        "top_passages": suspected[:10] or results[:10],
        "ai_detection": ai_detection,
    }

    if output_path is not None:
        write_json(Path(output_path), report)

    return report
