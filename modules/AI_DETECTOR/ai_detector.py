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
        "scoring_mode": mode,
        "model": mod,
        "heuristic": heur,
    }


def summarize_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not results:
        return {
            "passages_count": 0,
            "average_ai_score": None,
            "risk_level": "unknown",
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

    risk_level = "high" if high_count else ("medium" if medium_count else "low")
    docs = sorted(set(str(x.get("document") or "") for x in results if x.get("document")))

    return {
        "passages_count": len(results),
        "average_ai_score": round(avg, 4),
        "risk_level": risk_level,
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
