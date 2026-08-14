# -*- coding: utf-8 -*-

"""
article_card_builder.py

Phase 2D — Construction des Article Cards EnnoScholar.

Version V3.1 — Deep Technical Reading Adaptive + Narrative Capsule

Objectif :
- prendre les articles gardés par le consultant ;
- générer A1, A2, A3... ;
- charger le texte complet extrait depuis :
    1) fulltext/extracted_uploaded
    2) fulltext/extracted_direct
    3) fulltext/extracted
- ignorer les JSON d'échec / anti-robot / text_chars=0 ;
- nettoyer le texte extrait ;
- détecter les sections utiles ;
- générer une fiche article fiable via :
    extractive / template / llm / auto ;
- extraire les limitations explicites, contraintes méthodologiques et gaps ;
- extraire l'analyse technique profonde de la méthode/concept :
    problème -> chaîne de mécanisme -> flux données/modèle/validation -> résultat démontré -> limite -> transposition CIR ;
- produire une capsule narrative exploitable par Phase 4.5 / 4.7 / 5 pour écrire comme un consultant ;
- sauvegarder les cartes et leur index uniquement dans PostgreSQL.

Modes :
- template : rapide, déterministe, sans LLM
- llm      : meilleur, JSON strict, fallback template si échec
- auto     : LLM si disponible, sinon template

Important CIR :
- Article Cards = seules sources scientifiques ;
- Memory V2 ne doit jamais être utilisée ici ;
- Les limites doivent porter sur le concept/méthode, pas seulement sur l'article ;
- Le writer Phase 5 pourra expliquer techniquement les méthodes sans inventer ;
- Les champs Article Cards restent compatibles avec les anciennes phases.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from functools import lru_cache
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from sqlalchemy.orm import Session

from db.models import Article, Project, ScholarRun
from services.scholar_selection_scope import get_current_selected_articles


# ============================================================
# Config
# ============================================================

CARD_MODE = os.getenv("ENNOSCHOLAR_ARTICLE_CARD_MODE", "auto").lower()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv(
    "ENNOSCHOLAR_ARTICLE_CARD_LLM_MODEL",
    os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct"),
)

# Optimisation importante :
# - adaptive : défaut recommandé permanent. 1 appel LLM par article ; appels supplémentaires seulement si la capsule est faible.
# - core     : 1 appel LLM par article, jamais d'appel supplémentaire.
# - full     : ancien comportement riche, environ 3 appels LLM par article. À réserver aux audits ponctuels.
# - off      : aucun LLM, template uniquement.
LLM_STRATEGY = os.getenv("ENNOSCHOLAR_ARTICLE_CARD_LLM_STRATEGY", "extractive").lower()
DISABLE_LLM = os.getenv("ENNOSCHOLAR_ARTICLE_CARD_DISABLE_LLM", "false").lower() in {"1", "true", "yes", "on"}
SMART_REUSE = os.getenv("ENNOSCHOLAR_ARTICLE_CARD_SMART_REUSE", "true").lower() in {"1", "true", "yes", "on"}

# V3.1 — Adaptive full mode : pour tous les projets, on évite 3 appels LLM par article.
# Le second niveau LLM n'est lancé que si la capsule technique est insuffisante.
ADAPTIVE_EXTRA_LLM_ON_WEAK_CARD = os.getenv("ENNOSCHOLAR_ARTICLE_CARD_ADAPTIVE_EXTRA_LLM", "true").lower() in {"1", "true", "yes", "on"}
ADAPTIVE_CAPSULE_MIN_SCORE = int(os.getenv("ENNOSCHOLAR_ARTICLE_CARD_ADAPTIVE_MIN_SCORE", "7"))

LLM_TIMEOUT = int(os.getenv("ENNOSCHOLAR_ARTICLE_CARD_LLM_TIMEOUT", "180"))
MAX_CONTEXT_CHARS = int(os.getenv("ENNOSCHOLAR_ARTICLE_CARD_MAX_CONTEXT_CHARS", "14000"))
OLLAMA_NUM_CTX = int(os.getenv("ENNOSCHOLAR_ARTICLE_CARD_OLLAMA_NUM_CTX", "8192"))
OLLAMA_NUM_PREDICT = int(os.getenv("ENNOSCHOLAR_ARTICLE_CARD_OLLAMA_NUM_PREDICT", "1200"))
# V3.0 — Deep technical reading:
# - Le LLM principal doit extraire une capsule narrative complète dans le même appel.
# - Le template construit aussi une version déterministe si le LLM échoue.
DEEP_TECHNICAL_READING_ENABLED = os.getenv("ENNOSCHOLAR_ARTICLE_CARD_DEEP_READING", "true").lower() in {"1", "true", "yes", "on"}
TECHNICAL_CHAIN_MAX_STEPS = int(os.getenv("ENNOSCHOLAR_ARTICLE_CARD_CHAIN_MAX_STEPS", "7"))
TECHNICAL_CAPSULE_MAX_CHARS = int(os.getenv("ENNOSCHOLAR_ARTICLE_CARD_CAPSULE_MAX_CHARS", "2200"))

# V3.2 — Extractive first / no LLM by default.
# Phase 2 ne doit plus obligatoirement résumer avec un LLM : elle extrait les paragraphes originaux
# qui expliquent le problème, la méthode, les étapes, les définitions, les datasets, la validation,
# les résultats et les limites. Les phases 4.5/4.7/5 utiliseront ensuite ces preuves pour raisonner/rédiger.
EXTRACTIVE_READING_ENABLED = os.getenv("ENNOSCHOLAR_ARTICLE_CARD_EXTRACTIVE_READING", "true").lower() in {"1", "true", "yes", "on"}
EXTRACTIVE_ONLY_PHASE2 = os.getenv("ENNOSCHOLAR_ARTICLE_CARD_EXTRACTIVE_ONLY", "true").lower() in {"1", "true", "yes", "on"}
EXTRACTIVE_MAX_PARAGRAPHS_PER_BUCKET = int(os.getenv("ENNOSCHOLAR_ARTICLE_CARD_EXTRACTIVE_MAX_PARAGRAPHS", "8"))
EXTRACTIVE_MAX_PARAGRAPH_CHARS = int(os.getenv("ENNOSCHOLAR_ARTICLE_CARD_EXTRACTIVE_MAX_PARAGRAPH_CHARS", "1400"))
EXTRACTIVE_FULLTEXT_SCAN_CHARS = int(os.getenv("ENNOSCHOLAR_ARTICLE_CARD_EXTRACTIVE_FULLTEXT_SCAN_CHARS", "70000"))
EXTRACTIVE_REASONING_CONTEXT_CHARS = int(os.getenv("ENNOSCHOLAR_ARTICLE_CARD_EXTRACTIVE_REASONING_CONTEXT_CHARS", "14000"))

MIN_USEFUL_FULLTEXT_CHARS = int(os.getenv("ENNOSCHOLAR_CARD_MIN_FULLTEXT_CHARS", "1000"))
MAX_SECTION_CHARS = int(os.getenv("ENNOSCHOLAR_CARD_MAX_SECTION_CHARS", "3500"))

REUSABLE_CARD_STATUSES = {"valid", "valid_with_warnings"}
DIRECT_FULLTEXT_PIPELINE = "direct_known_urls_fulltext_v1"
LEGAL_FULLTEXT_PIPELINE = "legal_mcp_fulltext_v2"

# Cache mémoire pour éviter d'exécuter SciBERT deux fois sur le même article
# pendant un même run : une fois pour construire le contexte LLM, puis une fois
# lors de la construction de la card finale.
_SCIENTIFIC_EXTRACTION_CACHE: Dict[str, Dict[str, Any]] = {}

# ============================================================
# Scientific NER / SciBERT config
# ============================================================

# Cette couche est optionnelle et non bloquante : si transformers/torch ou le modèle
# ne sont pas disponibles, le builder continue en fallback déterministe.
SCIENTIFIC_NER_ENABLED = os.getenv("ENNOSCHOLAR_SCIENTIFIC_NER_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
SCIENTIFIC_NER_MODEL = os.getenv(
    "ENNOSCHOLAR_SCIENTIFIC_NER_MODEL",
    "JonyC/scibert-NER-finetuned-improved",
)
# ENNOSCHOLAR_SCIENTIFIC_NER_DEVICE=auto utilise CUDA si disponible, sinon CPU.
# Mettre 0 pour forcer GPU 0, -1 pour forcer CPU.
SCIENTIFIC_NER_DEVICE_RAW = os.getenv("ENNOSCHOLAR_SCIENTIFIC_NER_DEVICE", "auto").strip().lower()
SCIENTIFIC_NER_MAX_CHARS = int(os.getenv("ENNOSCHOLAR_SCIENTIFIC_NER_MAX_CHARS", "7000"))
SCIENTIFIC_NER_CHUNK_CHARS = int(os.getenv("ENNOSCHOLAR_SCIENTIFIC_NER_CHUNK_CHARS", "900"))
SCIENTIFIC_NER_BATCH_SIZE = max(1, int(os.getenv("ENNOSCHOLAR_SCIENTIFIC_NER_BATCH_SIZE", "8")))
SCIENTIFIC_NER_TOP_K = int(os.getenv("ENNOSCHOLAR_SCIENTIFIC_NER_TOP_K", "20"))
SCIENTIFIC_NER_MIN_SCORE = float(os.getenv("ENNOSCHOLAR_SCIENTIFIC_NER_MIN_SCORE", "0.62"))
SCIENTIFIC_NER_RUN_ON_FULLTEXT = os.getenv("ENNOSCHOLAR_SCIENTIFIC_NER_FULLTEXT", "false").lower() in {"1", "true", "yes", "on"}
SCIENTIFIC_NER_ALLOW_DOWNLOAD = os.getenv(
    "ENNOSCHOLAR_SCIENTIFIC_NER_ALLOW_DOWNLOAD", "false"
).lower() in {"1", "true", "yes", "on"}


# ============================================================
# Base helpers
# ============================================================

def _safe_text(value: Any, max_chars: int = 0) -> str:
    if value is None:
        return ""

    text = str(value).replace("\x00", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    if max_chars and len(text) > max_chars:
        return text[:max_chars].strip()

    return text


def _strip_accents(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _slugify(value: Any, max_len: int = 80) -> str:
    text = _strip_accents(str(value or "")).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or "unknown")[:max_len].strip("_")


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json_read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def _json_dump(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _word_count(text: str) -> int:
    return len(re.findall(r"\w+", text or ""))


def _sentences(text: str) -> List[str]:
    text = _safe_text(text)
    if not text:
        return []

    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if len(p.strip()) >= 25]


def _limit_sentences(text: str, max_sentences: int = 4, max_chars: int = 900) -> str:
    sents = _sentences(text)
    if not sents:
        return _safe_text(text, max_chars)

    out = " ".join(sents[:max_sentences])
    return _safe_text(out, max_chars)



# ============================================================
# Scientific entity extraction — SciBERT NER + safe fallback
# ============================================================

def _norm_key(value: Any) -> str:
    text = _strip_accents(_safe_text(value).lower())
    text = re.sub(r"[^a-z0-9+#.\-\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_generic_scientific_candidate(term: str) -> bool:
    """
    Filtre générique multi-domaines.
    Principe : SciBERT propose des candidats, mais seuls les termes assez spécifiques
    peuvent alimenter method_name / Phase 4.5. Les mots isolés génériques sont rejetés.
    """
    raw = _safe_text(term, 140).strip(" -_:;,.()[]{}\"'")
    n = _norm_key(raw)

    if not n or len(n) < 2:
        return True

    words = n.split()

    hard_generic = {
        "a", "an", "the", "this", "that", "these", "those", "and", "or", "of", "for", "with",
        "article", "paper", "study", "work", "research", "literature", "survey", "review",
        "approach", "method", "methods", "technique", "techniques", "model", "models",
        "framework", "system", "systems", "algorithm", "algorithms", "analysis", "result", "results",
        "discussion", "conclusion", "exploring", "investigating", "evaluation", "evaluating",
        "interaction", "classification", "prediction", "detection", "segmentation", "task", "tasks",
        "data", "dataset", "datasets", "image", "images", "performance", "accuracy", "precision",
        "recall", "training", "testing", "proposed", "based", "using", "learning", "deep", "neural",
        "network", "networks", "architecture", "problem", "problems", "maximum", "minimum",
        "parameter", "parameters", "magnitude", "magnitudes", "distance", "sampling", "spatial",
        "imagery", "synthesis", "synthesize", "synthesized", "augmentation", "convolution",
        "convolutional", "occluded", "perturbed", "discriminative", "persistence", "parametrized",
        "ablation", "generic", "base", "simple", "new", "novel", "efficient", "robust",
    }

    # Termes isolés : on garde surtout acronymes, CamelCase, termes avec chiffres/tirets/symboles.
    if len(words) == 1:
        if n in hard_generic:
            return True
        has_acronym = bool(re.fullmatch(r"[A-Z0-9][A-Z0-9+\-]{1,15}", raw))
        has_camel = bool(re.search(r"[a-z][A-Z]", raw))
        has_digit = bool(re.search(r"\d", raw))
        has_symbol = any(x in raw for x in ["-", "+", "/"])
        # Un mot minuscule seul est rarement un concept exploitable pour 4.5.
        if raw.lower() == raw and not (has_digit or has_symbol):
            return True
        if not (has_acronym or has_camel or has_digit or has_symbol):
            return True

    # Phrases trop courtes ou trop longues sans spécificité.
    if len(words) > 8:
        return True

    # Si tous les mots sont génériques, on rejette.
    if words and all(w in hard_generic for w in words):
        return True

    # Un terme multi-mots doit avoir au moins un signal technique spécifique.
    if len(words) >= 2:
        technical_signal = bool(re.search(
            r"\b(method|approach|algorithm|architecture|model|framework|protocol|benchmark|dataset|"
            r"augmentation|neural network|object detection|image classification|feature selection|"
            r"dimensionality reduction|uncertainty quantification|synthetic data|simulation|"
            r"measurement|validation|optimization|regularization|l0|l1|l2)\b",
            n,
            flags=re.I,
        ))
        has_acronym_inside = bool(re.search(r"\b[A-Z]{2,}\b", raw))
        has_camel_inside = bool(re.search(r"[a-z][A-Z]", raw))
        if not (technical_signal or has_acronym_inside or has_camel_inside):
            # On garde une marge pour les groupes nominaux vraiment spécifiques avec tirets/chiffres.
            if not re.search(r"[0-9+\-/]", raw):
                return True

    return False


def _is_strong_phase45_entity(item: Dict[str, Any]) -> bool:
    """Filtre final : ce qui va réellement aider Phase 4.5."""
    term = _safe_text(item.get("term"), 140)
    n = _norm_key(term)
    if _is_generic_scientific_candidate(term):
        return False

    sections = set(item.get("sections") or [])
    occ = int(item.get("occurrences") or 0)
    labels = set(item.get("labels") or [])

    # Signaux forts : titre/méthode/abstract, répétition, acronyme/norme technique, fallback confirmé.
    in_strong_section = bool(sections & {"title", "abstract", "method"})
    repeated = occ >= 2
    specific_shape = bool(
        re.fullmatch(r"[A-Z0-9][A-Z0-9+\-]{1,15}", term)
        or re.search(r"[a-z][A-Z]", term)
        or re.search(r"\d", term)
        or "-" in term
    )
    technical_phrase = bool(re.search(
        r"\b(data augmentation|neural network|generative model|synthetic data|simulation|"
        r"measurement protocol|feature selection|uncertainty quantification|"
        r"object detection|image classification|validation protocol|l0\s*-?\s*norm)\b",
        n,
        flags=re.I,
    ))

    return (in_strong_section and repeated) or specific_shape or technical_phrase or "FALLBACK_TERM" in labels

def _entity_sentence_context(text: str, start: int, end: int, max_chars: int = 420) -> str:
    text = text or ""
    start = max(0, int(start or 0))
    end = min(len(text), int(end or start))

    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start), text.rfind(";", 0, start))
    right_candidates = [x for x in [text.find(".", end), text.find("\n", end), text.find(";", end)] if x != -1]
    right = min(right_candidates) if right_candidates else min(len(text), end + max_chars)

    if left == -1:
        left = max(0, start - max_chars // 2)
    else:
        left += 1

    sent = clean_article_text(text[left:right + 1])
    return _safe_text(sent, max_chars)


def _section_blocks_for_scientific_ner(
    article: Article,
    abstract: str,
    sections: Dict[str, str],
    full_text: str = "",
) -> List[Dict[str, str]]:
    blocks = [
        {"section": "title", "text": _safe_text(getattr(article, "title", ""), 500)},
        {"section": "abstract", "text": _safe_text(abstract, 2500)},
        {"section": "method", "text": _safe_text(sections.get("method"), 4500)},
        {"section": "experiments", "text": _safe_text(sections.get("experiments"), 2500)},
        {"section": "results", "text": _safe_text(sections.get("results"), 2500)},
        {"section": "limitations", "text": _safe_text(sections.get("limitations"), 2000)},
        {"section": "conclusion", "text": _safe_text(sections.get("conclusion"), 2000)},
    ]

    if full_text and SCIENTIFIC_NER_RUN_ON_FULLTEXT:
        blocks.append({"section": "full_text_preview", "text": _safe_text(full_text, 2000)})

    return [b for b in blocks if b.get("text")]


def _resolve_scientific_ner_device() -> int:
    """Retourne 0 si CUDA est disponible en mode auto, sinon -1. Compatible transformers.pipeline."""
    raw = SCIENTIFIC_NER_DEVICE_RAW
    if raw in {"cpu", "-1"}:
        return -1
    if raw in {"cuda", "gpu", "auto"}:
        try:
            import torch
            return 0 if torch.cuda.is_available() else -1
        except Exception:
            return -1
    try:
        return int(raw)
    except Exception:
        return -1


@lru_cache(maxsize=1)
def _get_scientific_ner_pipeline():
    if not SCIENTIFIC_NER_ENABLED:
        return None

    try:
        from transformers import (
            AutoModelForTokenClassification,
            AutoTokenizer,
            pipeline,
        )

        device = _resolve_scientific_ner_device()
        device_label = "cuda:0" if device >= 0 else "cpu"
        print(
            f"[EnnoScholar][ArticleCards][SciNER] loading model={SCIENTIFIC_NER_MODEL} device={device_label}",
            flush=True,
        )
        model_source = SCIENTIFIC_NER_MODEL
        if not SCIENTIFIC_NER_ALLOW_DOWNLOAD:
            from huggingface_hub import snapshot_download

            model_source = snapshot_download(
                repo_id=SCIENTIFIC_NER_MODEL,
                local_files_only=True,
            )
        tokenizer = AutoTokenizer.from_pretrained(
            model_source,
            local_files_only=not SCIENTIFIC_NER_ALLOW_DOWNLOAD,
        )
        model = AutoModelForTokenClassification.from_pretrained(
            model_source,
            local_files_only=not SCIENTIFIC_NER_ALLOW_DOWNLOAD,
        )
        return pipeline(
            "token-classification",
            model=model,
            tokenizer=tokenizer,
            aggregation_strategy="simple",
            device=device,
        )
    except Exception as exc:
        print(
            f"[EnnoScholar][ArticleCards][SciNER] disabled_or_unavailable model={SCIENTIFIC_NER_MODEL} error={exc}",
            flush=True,
        )
        return None

def _chunk_text_for_ner(text: str, max_chars: int = SCIENTIFIC_NER_CHUNK_CHARS) -> List[Tuple[int, str]]:
    text = _safe_text(text, SCIENTIFIC_NER_MAX_CHARS)
    if not text:
        return []

    chunks: List[Tuple[int, str]] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            cut = max(text.rfind(". ", start, end), text.rfind("\n", start, end))
            if cut > start + 400:
                end = cut + 1
        chunks.append((start, text[start:end]))
        start = end
    return chunks


def _fallback_scientific_terms_from_text(blocks: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """
    Fallback non-domaine : extrait acronymes, CamelCase, formes avec tirets,
    et groupes nominaux techniques courts. Ne remplace pas SciBERT, mais évite
    une panne totale si le modèle n'est pas disponible.
    """
    candidates: List[Dict[str, Any]] = []

    noun_phrase_re = re.compile(
        r"\b(?:[A-Za-z][A-Za-z0-9+\-/]{2,}\s+){1,5}"
        r"(?:model|network|framework|augmentation|classification|detection|segmentation|"
        r"invariance|robustness|generalization|selection|reduction|learning|generation|"
        r"diffusion|transformer|encoder|decoder|dataset|benchmark|metric)s?\b",
        flags=re.I,
    )

    special_re = re.compile(
        r"\b(?:[A-Z]{2,8}[A-Za-z0-9+\-]*|[A-Z][a-z]+[A-Z][A-Za-z0-9]+|[A-Za-z]+-[A-Za-z0-9\-]+)\b"
    )

    for block in blocks:
        section = block.get("section") or "unknown"
        text = block.get("text") or ""

        for rx, base_score in [(special_re, 0.72), (noun_phrase_re, 0.64)]:
            for m in rx.finditer(text):
                term = _safe_text(m.group(0), 120).strip(" -_:;,.()[]{}")
                if _is_generic_scientific_candidate(term):
                    continue
                candidates.append({
                    "term": term,
                    "label": "FALLBACK_TERM",
                    "score": base_score + (0.12 if section == "title" else 0.0) + (0.06 if section == "method" else 0.0),
                    "section": section,
                    "source_sentence": _entity_sentence_context(text, m.start(), m.end()),
                    "extractor": "fallback_regex",
                })

    return candidates


def _classify_scientific_entity(term: str, sentence: str = "", section: str = "") -> str:
    n = _norm_key(f"{term} {sentence}")
    t = _safe_text(term)

    if re.search(r"\b(dataset|benchmark|corpus|test set|validation set|evaluation protocol)\b", n):
        return "dataset_candidate"

    if re.search(r"\b(accuracy|precision|recall|f1|auc|iou|map|metric|score|error rate|loss|asr|l0|l1|l2|linf)\b", n):
        return "metric_candidate"

    if re.search(r"\b(gan|cnn|rnn|lstm|bert|transformer|diffusion|adversarial|sparse|augmentation|generation|"
                 r"feature selection|dimensionality reduction|phase history|synthetic data|remote sensing|"
                 r"object detection|image classification|uncertainty quantification|u-net|resnet|efficientnet)\b", n):
        return "method_candidate"

    if re.fullmatch(r"[A-Z0-9][A-Z0-9+\-]{1,15}", t) or re.search(r"[a-z][A-Z]", t):
        return "technical_concept_candidate"

    if section in {"method", "title"}:
        return "technical_concept_candidate"

    return "scientific_term"

def _merge_and_rank_scientific_entities(candidates: List[Dict[str, Any]], top_k: int = SCIENTIFIC_NER_TOP_K) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}

    for c in candidates:
        term = _safe_text(c.get("term"), 120).strip(" -_:;,.()[]{}")
        if _is_generic_scientific_candidate(term):
            continue

        key = _norm_key(term)
        if not key:
            continue

        score = float(c.get("score") or 0.0)
        section = c.get("section") or "unknown"
        sentence = _safe_text(c.get("source_sentence"), 500)
        usage = _classify_scientific_entity(term, sentence, section)

        section_bonus = {
            "title": 0.20,
            "abstract": 0.10,
            "method": 0.18,
            "experiments": 0.08,
            "results": 0.06,
        }.get(section, 0.0)

        final_score = min(1.0, score + section_bonus)

        if key not in merged:
            merged[key] = {
                "term": term,
                "normalized": key,
                "usage": usage,
                "labels": [],
                "score": final_score,
                "occurrences": 0,
                "sections": [],
                "source_sentences": [],
                "extractors": [],
            }

        item = merged[key]
        item["score"] = max(float(item.get("score") or 0), final_score)
        item["occurrences"] = int(item.get("occurrences") or 0) + 1

        label = _safe_text(c.get("label"), 80)
        if label and label not in item["labels"]:
            item["labels"].append(label)

        if section and section not in item["sections"]:
            item["sections"].append(section)

        if sentence and sentence not in item["source_sentences"]:
            item["source_sentences"].append(sentence)

        extractor = _safe_text(c.get("extractor"), 80)
        if extractor and extractor not in item["extractors"]:
            item["extractors"].append(extractor)

    ranked = list(merged.values())
    for item in ranked:
        occ_bonus = min(0.15, int(item.get("occurrences") or 0) * 0.03)
        sec_bonus = 0.08 if "title" in item.get("sections", []) else 0.0
        item["score"] = round(min(1.0, float(item.get("score") or 0) + occ_bonus + sec_bonus), 3)
        item["source_sentences"] = item.get("source_sentences", [])[:3]

    # Filtre final pour éviter que Phase 4.5 utilise des mots trop génériques.
    ranked = [x for x in ranked if _is_strong_phase45_entity(x)]

    ranked.sort(
        key=lambda x: (
            -float(x.get("score") or 0),
            0 if x.get("usage") in {"method_candidate", "technical_concept_candidate"} else 1,
            -int(x.get("occurrences") or 0),
            x.get("term") or "",
        )
    )

    return ranked[:top_k]


def extract_scientific_entities(
    article: Article,
    abstract: str,
    sections: Dict[str, str],
    full_text: str = "",
    use_model: bool = True,
) -> Dict[str, Any]:
    cache_key = f"{getattr(article, 'id', 'unknown')}:{len(full_text or '')}:{SCIENTIFIC_NER_MAX_CHARS}:{SCIENTIFIC_NER_TOP_K}:{SCIENTIFIC_NER_MODEL}:{SCIENTIFIC_NER_DEVICE_RAW}:model={int(bool(use_model))}"
    cached = _SCIENTIFIC_EXTRACTION_CACHE.get(cache_key)
    if isinstance(cached, dict):
        return cached

    blocks = _section_blocks_for_scientific_ner(article, abstract, sections, full_text=full_text)
    ner = _get_scientific_ner_pipeline() if use_model else None
    candidates: List[Dict[str, Any]] = []
    source = "scibert_ner" if ner else "instant_regex" if not use_model else "fallback_regex"

    if ner:
        # Le pipeline recevait auparavant un chunk à la fois : sur GPU, cela
        # créait de nombreux micro-appels sous-utilisant CUDA. Les chunks d'un
        # même article sont désormais traités par petits lots.
        work_items: List[Tuple[str, str]] = []
        for block in blocks:
            section = block.get("section") or "unknown"
            for _offset, chunk in _chunk_text_for_ner(block.get("text") or ""):
                work_items.append((section, chunk))

        for start in range(0, len(work_items), SCIENTIFIC_NER_BATCH_SIZE):
            batch = work_items[start:start + SCIENTIFIC_NER_BATCH_SIZE]
            try:
                batch_outputs = ner(
                    [chunk for _section, chunk in batch],
                    batch_size=SCIENTIFIC_NER_BATCH_SIZE,
                )
            except Exception as exc:
                print(f"[EnnoScholar][ArticleCards][SciNER] batch_inference_error error={exc}", flush=True)
                batch_outputs = [[] for _ in batch]

            # transformers renvoie une liste d'entités par entrée pour la
            # token-classification ; le garde-fou couvre aussi les vieux
            # retours plats d'un pipeline personnalisé.
            if len(batch) == 1 and batch_outputs and isinstance(batch_outputs[0], dict):
                batch_outputs = [batch_outputs]

            for (section, chunk), outputs in zip(batch, batch_outputs or []):
                for ent in outputs or []:
                    term = _safe_text(ent.get("word") or ent.get("entity_group") or "", 120)
                    term = term.replace(" ##", "").replace("##", "")
                    score = float(ent.get("score") or 0.0)
                    if score < SCIENTIFIC_NER_MIN_SCORE:
                        continue
                    if _is_generic_scientific_candidate(term):
                        continue

                    start = int(ent.get("start") or 0)
                    end = int(ent.get("end") or start + len(term))
                    candidates.append({
                        "term": term,
                        "label": _safe_text(ent.get("entity_group") or ent.get("entity") or "NER", 80),
                        "score": score,
                        "section": section,
                        "source_sentence": _entity_sentence_context(chunk, start, end),
                        "extractor": "scibert_ner",
                    })

    # Fallback toujours ajouté, même avec SciBERT, pour capter acronymes/noms comme GAN, U-Net, AdvMask.
    candidates.extend(_fallback_scientific_terms_from_text(blocks))
    ranked = _merge_and_rank_scientific_entities(candidates, top_k=SCIENTIFIC_NER_TOP_K)

    method_candidates = [x for x in ranked if x.get("usage") in {"method_candidate", "technical_concept_candidate"}][:12]
    dataset_candidates = [x for x in ranked if x.get("usage") == "dataset_candidate"][:8]
    metric_candidates = [x for x in ranked if x.get("usage") == "metric_candidate"][:8]

    key_passages = []
    seen_passages = set()
    for ent in ranked[:18]:
        for sent in ent.get("source_sentences") or []:
            k = _norm_key(sent)[:220]
            if not k or k in seen_passages:
                continue
            seen_passages.add(k)
            key_passages.append({
                "term": ent.get("term"),
                "usage": ent.get("usage"),
                "section": (ent.get("sections") or ["unknown"])[0],
                "sentence": sent,
            })
            break

    extraction = {
        "enabled": bool(SCIENTIFIC_NER_ENABLED and use_model),
        "model": SCIENTIFIC_NER_MODEL if use_model else None,
        "device": ("cuda:0" if _resolve_scientific_ner_device() >= 0 else "cpu") if use_model else "not_used",
        "source": source,
        "entities_count": len(ranked),
        "technical_entities": ranked,
        "method_candidates": method_candidates,
        "dataset_candidates": dataset_candidates,
        "metric_candidates": metric_candidates,
        "key_passages": key_passages[:12],
        "rules": {
            "ner_is_candidate_generator_only": True,
            "entities_do_not_replace_article_evidence": True,
            "source_sentences_required_for_phase_4_5": True,
            "generic_terms_filtered": True,
            "cached_per_run": True,
            "instant_mode_skips_model_loading": not use_model,
        },
    }
    _SCIENTIFIC_EXTRACTION_CACHE[cache_key] = extraction
    return extraction


def _best_method_candidate_from_scientific_entities(card: Dict[str, Any]) -> str:
    extraction = _as_dict(card.get("scientific_entity_extraction"))
    candidates = extraction.get("method_candidates") or []

    for c in candidates:
        term = _safe_text(c.get("term"), 120)
        if term and not _is_generic_scientific_candidate(term):
            return term

    return ""


def enrich_card_with_scientific_entities(
    card: Dict[str, Any],
    article: Article,
    abstract: str,
    sections: Dict[str, str],
    full_text: str,
    use_model: bool = True,
) -> Dict[str, Any]:
    extraction = extract_scientific_entities(
        article,
        abstract,
        sections,
        full_text=full_text,
        use_model=use_model,
    )
    card["scientific_entity_extraction"] = extraction
    card["scientific_entities"] = extraction.get("technical_entities") or []
    card["key_scientific_passages"] = extraction.get("key_passages") or []

    # mots_cles = termes validés, pas forcément method_name.
    existing = [_safe_text(x, 80) for x in card.get("mots_cles", []) if _safe_text(x, 80)]
    entity_terms = [
        _safe_text(x.get("term"), 80)
        for x in extraction.get("technical_entities", [])
        if _safe_text(x.get("term"), 80)
    ]

    merged: List[str] = []
    seen = set()
    for term in existing + entity_terms:
        key = _norm_key(term)
        if not key or key in seen or _is_generic_scientific_candidate(term):
            continue
        seen.add(key)
        merged.append(term)
        if len(merged) >= 18:
            break

    card["mots_cles"] = merged
    card.setdefault("evidence", {})["scientific_ner_enabled"] = bool(SCIENTIFIC_NER_ENABLED and use_model)
    card.setdefault("evidence", {})["scientific_ner_model"] = SCIENTIFIC_NER_MODEL if use_model else None
    card.setdefault("evidence", {})["scientific_entities_count"] = extraction.get("entities_count", 0)
    return card

def _project_ennoscholar_dir(project: Project) -> Path:
    root = Path(os.getenv("ENNOSMART_STORAGE_ROOT", "C:/EnnoSmart/storage"))

    organisme = _slugify(getattr(project, "organisme", "") or "organisme")
    project_name = _slugify(getattr(project, "project_name", "") or "project")
    year = _slugify(getattr(project, "year", "") or "year")

    return (
        root
        / "organismes"
        / organisme
        / "projects"
        / project_name
        / "years"
        / year
        / "ennoscholar"
    )


def _article_file_prefix(article: Article) -> str:
    return f"article_{article.id}_{_slugify(article.title or 'article', 60)}"


def _article_cards_dir(project: Project, scope_id: str | None = None) -> Path:
    base = _project_ennoscholar_dir(project) / "state_of_art_payload" / "article_cards"
    return base / "conversation_scopes" / _slugify(scope_id, 100) if scope_id else base


def _article_cards_payload_path(project: Project, scope_id: str | None = None) -> Path:
    return _article_cards_dir(project, scope_id) / "article_cards_payload.json"


def _article_card_path(
    project: Project,
    article: Article,
    scope_id: str | None = None,
) -> Path:
    filename = (
        f"article_{int(article.id)}_card.json"
        if scope_id
        else f"{_article_file_prefix(article)}_card.json"
    )
    return _article_cards_dir(project, scope_id) / filename


def _article_cards_db_uri(run_id: int, scope_id: str | None = None) -> str:
    suffix = f"/scopes/{_slugify(scope_id, 100)}" if scope_id else ""
    return f"db://scholar_runs/{int(run_id)}/article_cards_payload{suffix}"


def _current_scholar_run_for_cards(db: Session, project: Project) -> ScholarRun | None:
    return (
        db.query(ScholarRun)
        .filter(ScholarRun.project_id == int(project.id))
        .filter(ScholarRun.status != "improvement_corpus")
        .order_by(ScholarRun.created_at.desc(), ScholarRun.id.desc())
        .first()
    )


def _save_article_cards_payload_to_db(
    db: Session,
    project: Project,
    payload: Dict[str, Any],
    scope_id: str | None = None,
) -> Dict[str, Any]:
    run = _current_scholar_run_for_cards(db, project)
    if run is None:
        raise RuntimeError("Aucun ScholarRun courant pour stocker les Article Cards.")
    uri = _article_cards_db_uri(int(run.id), scope_id)
    saved = dict(payload or {})
    saved["payload_path"] = uri
    saved["output_path"] = uri
    saved["storage_mode"] = "database_only"
    raw = dict(run.raw_result_json or {})
    if scope_id:
        scopes = dict(raw.get("article_cards_payload_by_scope") or {})
        scopes[str(scope_id)] = saved
        raw["article_cards_payload_by_scope"] = scopes
    else:
        raw["article_cards_payload"] = saved
    run.raw_result_json = raw
    db.add(run)
    db.commit()
    return saved


# ============================================================
# Text cleaning
# ============================================================

def _repair_mojibake(text: str) -> str:
    if not text:
        return ""

    replacements = {
        "â€™": "’",
        "â€˜": "‘",
        "â€œ": "“",
        "â€": "”",
        "â€“": "–",
        "â€”": "—",
        "â€¦": "…",
        "Â±": "±",
        "Â·": "·",
        "Ã—": "×",
        "âˆ’": "−",
        "â‰¤": "≤",
        "â‰¥": "≥",
        "â‰ˆ": "≈",
        "â‚¬": "€",
        "ï¬": "fi",
        "ï¬‚": "fl",
        "ï¬ƒ": "ffi",
        "ï¬„": "ffl",
        "ï¬": "fi",
        "Abstractâ": "Abstract —",
        "Index Termsâ": "Index Terms —",
        "Keywordsâ": "Keywords —",
    }

    for bad, good in replacements.items():
        text = text.replace(bad, good)

    return text


def _remove_reference_section(text: str) -> str:
    patterns = [
        r"\n\s*references\s*\n",
        r"\n\s*bibliography\s*\n",
        r"\n\s*références\s*\n",
        r"\n\s*bibliographie\s*\n",
    ]

    lower_text = "\n" + text
    cut_positions = []

    for p in patterns:
        m = re.search(p, lower_text, flags=re.IGNORECASE)
        if m and m.start() > len(text) * 0.45:
            cut_positions.append(m.start())

    if cut_positions:
        return lower_text[:min(cut_positions)].strip()

    return text


def _remove_noisy_lines(text: str) -> str:
    lines = text.splitlines()
    cleaned: List[str] = []

    for line in lines:
        original = line
        line = line.strip()

        if not line:
            cleaned.append("")
            continue

        low = line.lower()

        if re.fullmatch(r"\d{1,4}", line):
            continue

        noise_markers = [
            "arxiv:",
            "copyright",
            "all rights reserved",
            "preprint submitted",
            "this work has been submitted",
            "journal of",
            "proceedings of",
            "ieee",
            "springer nature",
            "licensee",
            "downloaded from",
        ]

        if len(line) < 120 and any(m in low for m in noise_markers):
            continue

        if len(line) <= 2:
            continue

        cleaned.append(original)

    return "\n".join(cleaned)


def _join_broken_paragraphs(text: str) -> str:
    lines = [l.strip() for l in text.splitlines()]
    out: List[str] = []
    buffer = ""

    section_title_re = re.compile(
        r"^(\d+(\.\d+)*\.?\s+)?"
        r"(abstract|résumé|introduction|related work|background|method|methodology|materials and methods|"
        r"proposed method|proposed approach|experiments?|experimental setup|evaluation|results?|discussion|"
        r"limitations?|future work|conclusion|references|bibliography|état de l’art|méthode|résultats?|limites?)\b",
        flags=re.IGNORECASE,
    )

    for line in lines:
        if not line:
            if buffer:
                out.append(buffer.strip())
                buffer = ""
            out.append("")
            continue

        is_section = bool(section_title_re.match(line))
        is_short_title = len(line) < 80 and line.isupper()

        if is_section or is_short_title:
            if buffer:
                out.append(buffer.strip())
                buffer = ""
            out.append(line)
            continue

        if buffer.endswith("-"):
            buffer = buffer[:-1] + line
        elif buffer:
            buffer += " " + line
        else:
            buffer = line

    if buffer:
        out.append(buffer.strip())

    text = "\n".join(out)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)

    return text.strip()


def clean_article_text(raw_text: str) -> str:
    text = raw_text or ""
    text = _repair_mojibake(text)
    text = _remove_noisy_lines(text)
    text = _remove_reference_section(text)
    text = _join_broken_paragraphs(text)
    return _safe_text(text)


def _has_mojibake(text: str) -> bool:
    markers = ["Ã", "Â", "â€™", "â€œ", "â€", "ï¬", "�"]
    return any(m in (text or "") for m in markers)



# ============================================================
# Fulltext loader
# ============================================================

def _dedupe_candidate_paths(candidates: List[Tuple[str, Path]]) -> List[Tuple[str, Path]]:
    out: List[Tuple[str, Path]] = []
    seen = set()
    for source_kind, path in candidates:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((source_kind, path))
    return out


def _legacy_fulltext_matches(folder: Path, article: Article, suffix: str) -> List[Path]:
    """Retrouve les anciens noms, y compris __legal/__direct/__uploaded."""
    if not folder.exists():
        return []

    article_id = int(article.id)
    suffix = suffix.lstrip("_")
    pattern = re.compile(
        rf"^article_{article_id}_.+_+{re.escape(suffix)}$",
        flags=re.IGNORECASE,
    )

    matches = [
        path
        for path in folder.glob(f"article_{article_id}_*.json")
        if path.is_file() and pattern.match(path.name)
    ]
    matches.sort(
        key=lambda p: (
            "__" in p.name,
            p.name.count("__"),
            len(p.name),
            p.name.lower(),
        )
    )
    return matches


def _candidate_fulltext_paths(project: Project, article: Article) -> List[Tuple[str, Path]]:
    """Ordre de priorité : uploaded > legal > direct > saved_pdf."""
    base = _project_ennoscholar_dir(project) / "fulltext"
    prefix = _article_file_prefix(article)

    specs = [
        ("uploaded", base / "extracted_uploaded", f"{prefix}_uploaded_fulltext.json", "uploaded_fulltext.json"),
        ("legal", base / "extracted_legal", f"{prefix}_legal_fulltext.json", "legal_fulltext.json"),
        ("direct", base / "extracted_direct", f"{prefix}_direct_fulltext.json", "direct_fulltext.json"),
        ("saved_pdf", base / "extracted", f"{prefix}_fulltext.json", "fulltext.json"),
    ]

    candidates: List[Tuple[str, Path]] = []
    for source_kind, folder, canonical_name, suffix in specs:
        candidates.append((source_kind, folder / canonical_name))
        for legacy_path in _legacy_fulltext_matches(folder, article, suffix):
            candidates.append((source_kind, legacy_path))

    return _dedupe_candidate_paths(candidates)


def _extract_text_from_fulltext_json(data: Dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return ""

    pages = data.get("pages")
    if isinstance(pages, list) and pages:
        parts = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            text = page.get("text") or page.get("content") or page.get("clean_text") or ""
            if isinstance(text, str) and text.strip():
                parts.append(text)
        if parts:
            return "\n\n".join(parts)

    for key in [
        "clean_text", "full_text", "text", "content",
        "full_text_preview", "extracted_text", "markdown",
    ]:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value

    return ""


def _is_successful_fulltext_json(data: Dict[str, Any], raw_text: str) -> bool:
    if not isinstance(data, dict):
        return False

    status = str(data.get("status") or "").strip().lower()
    full_status = str(data.get("full_text_status") or "").strip().lower()

    bad_markers = [
        "pdf_url_found_but_download_blocked", "blocked_by_antibot", "antibot_blocked",
        "publisher_interstitial", "paywall_blocked", "no_pdf_url_found",
        "missing_or_blocked_pdf", "missing_or_blocked_fulltext", "missing_legal_fulltext",
        "not_pdf_response", "download_failed", "extract_failed", "extraction_failed",
        "legal_pdf_found_but_extraction_failed", "missing_pdf", "identity_mismatch",
        "remote_tls_error",
    ]

    if any(marker in status for marker in bad_markers):
        return False
    if any(marker in full_status for marker in bad_markers):
        return False
    if full_status != "text_extracted":
        return False
    if len((raw_text or "").strip()) < MIN_USEFUL_FULLTEXT_CHARS:
        return False
    return True


def _load_fulltext(project: Project, article: Article) -> Dict[str, Any]:
    candidates_info: List[Dict[str, Any]] = []

    # Le cache PostgreSQL global est prioritaire : il évite de dupliquer le
    # même texte dans chaque projet/run lorsque le DOI est déjà connu.
    try:
        from db.database import SessionLocal
        from services.scholar_fulltext_cache_service import get_cached_fulltext

        cache_db = SessionLocal()
        try:
            cached = get_cached_fulltext(cache_db, article)
        finally:
            cache_db.close()
    except Exception:
        cached = None

    if isinstance(cached, dict):
        raw_text = _extract_text_from_fulltext_json(cached)
        cleaned_text = clean_article_text(raw_text)
        if _is_successful_fulltext_json(cached, cleaned_text):
            retrieval_stage = _safe_text(cached.get("retrieval_stage"), 80).lower()
            source_kind = "legal" if "legal" in retrieval_stage or cached.get("retrieved_via_mcp") else "direct"
            return {
                "found": True,
                "source_kind": source_kind,
                "path": f"db://scholar_fulltext_cache/{cached.get('fulltext_cache_id')}",
                "text": cleaned_text,
                "pages_count": cached.get("pages_count") or len(cached.get("pages") or []),
                "text_chars": len(cleaned_text),
                "text_words": _word_count(cleaned_text),
                "source_status": cached.get("status"),
                "storage_mode": "global_database_cache",
                "quality": cached.get("quality") or {},
                "fulltext_provenance": {
                    "retrieved_via_mcp": bool(cached.get("retrieved_via_mcp")),
                    "provider": cached.get("legal_provider"),
                    "license": cached.get("license") or cached.get("legal_license"),
                    "same_article": True,
                    "verified_pdf": bool(cached.get("verified_pdf")),
                    "cache_hit": True,
                    "cache_key": cached.get("fulltext_cache_key"),
                },
                "fulltext_diagnostics": {"status": "global_database_cache_hit"},
                "candidates_info": [{
                    "source_kind": "global_database_cache",
                    "path": f"db://scholar_fulltext_cache/{cached.get('fulltext_cache_id')}",
                    "exists": True,
                    "selected": True,
                    "status": "cache_hit",
                }],
            }

    for source_kind, path in _candidate_fulltext_paths(project, article):
        data = _json_read(path)

        if not data:
            candidates_info.append({
                "source_kind": source_kind,
                "path": str(path),
                "exists": path.exists(),
                "selected": False,
                "reason": "missing_or_invalid_json",
            })
            continue

        raw_text = _extract_text_from_fulltext_json(data)
        cleaned_text = clean_article_text(raw_text)
        is_ok = _is_successful_fulltext_json(data, cleaned_text)

        diagnostic = (
            _fulltext_diagnostic_summary(data)
            if "_fulltext_diagnostic_summary" in globals()
            else {
                "status": data.get("status"),
                "full_text_status": data.get("full_text_status"),
            }
        )

        candidates_info.append({
            "source_kind": source_kind,
            "path": str(path),
            "exists": True,
            "selected": is_ok,
            "status": data.get("status"),
            "full_text_status": data.get("full_text_status"),
            "declared_text_chars": data.get("text_chars"),
            "raw_chars": len(raw_text or ""),
            "clean_chars": len(cleaned_text or ""),
            "diagnostic": diagnostic,
        })

        if not is_ok:
            continue

        return {
            "found": True,
            "source_kind": source_kind,
            "path": str(path),
            "text": cleaned_text,
            "pages_count": data.get("pages_count") or len(data.get("pages") or []),
            "text_chars": len(cleaned_text),
            "text_words": _word_count(cleaned_text),
            "source_status": data.get("status"),
            "storage_mode": data.get("storage_mode"),
            "quality": data.get("quality") or {},
            "fulltext_provenance": {
                "retrieved_via_mcp": bool(data.get("retrieved_via_mcp")),
                "provider": data.get("legal_provider"),
                "license": data.get("legal_license"),
                "version": data.get("legal_version"),
                "host_type": data.get("host_type"),
                "access_type": data.get("access_type"),
                "rights_status": data.get("rights_status"),
                "source_domain": data.get("source_domain"),
                "discovered_via": data.get("discovered_via"),
                "identity_score": data.get("identity_score"),
                "identity_method": data.get("identity_method"),
                "same_article": data.get("same_article"),
                "verified_pdf": data.get("verified_pdf"),
                "resolver_version": data.get("mcp_resolver_version"),
                "pdf_saved": bool(data.get("pdf_saved")),
            },
            "fulltext_diagnostics": diagnostic,
            "candidates_info": candidates_info,
        }

    return {
        "found": False,
        "source_kind": None,
        "path": None,
        "text": "",
        "pages_count": 0,
        "text_chars": 0,
        "text_words": 0,
        "fulltext_diagnostics": [
            item.get("diagnostic")
            for item in candidates_info
            if isinstance(item.get("diagnostic"), dict)
        ],
        "candidates_info": candidates_info,
    }


# ============================================================
# DB
# ============================================================

def get_selected_articles_for_project(db: Session, project: Project) -> List[Article]:
    return get_current_selected_articles(db, project)


# ============================================================
# Metadata extraction
# ============================================================

def _extract_authors(article: Article) -> List[str]:
    sj = _as_dict(getattr(article, "source_json", None))

    direct = getattr(article, "authors", None)
    if isinstance(direct, list):
        return [str(x).strip() for x in direct if str(x).strip()]

    for key in ["authors", "author_names", "authors_name"]:
        value = sj.get(key)
        if isinstance(value, list):
            names = []
            for item in value:
                if isinstance(item, str):
                    names.append(item)
                elif isinstance(item, dict):
                    names.append(item.get("name") or item.get("author_name") or "")
            return [n.strip() for n in names if n and n.strip()]

    return []


def _author_label(authors: List[str]) -> str:
    if not authors:
        return "Les auteurs"

    first = authors[0].strip()
    if not first:
        return "Les auteurs"

    last = first.split()[-1]

    if len(authors) == 1:
        return last

    return f"{last} et al."


def _extract_abstract_from_json(article: Article) -> str:
    sj = _as_dict(getattr(article, "source_json", None))
    summary = _as_dict(sj.get("article_summary"))

    for key in [
        "abstract",
        "abstract_original",
        "abstract_en",
        "abstract_fr",
        "summary",
        "resume",
        "résumé",
    ]:
        value = (
            summary.get(key)
            or sj.get(key)
            or getattr(article, key, None)
        )
        if value:
            return clean_article_text(_safe_text(value, 5000))

    return ""


# ============================================================
# Section extraction
# ============================================================

SECTION_TITLE_MAP = {
    "abstract": ["abstract", "résumé", "resume", "summary"],
    "introduction": ["introduction", "background", "context", "motivation"],
    "related_work": [
        "related work", "state of the art", "literature review",
        "état de l’art", "etat de l art", "travaux connexes",
    ],
    "method": [
        "method", "methods", "methodology", "materials and methods",
        "proposed method", "proposed approach", "approach",
        "framework", "algorithm", "algorithms", "model", "models",
        "méthode", "méthodes", "méthodologie", "algorithme",
        "methods and datasets", "method and datasets", "experimental method",
        "proposed model", "proposed framework",
        "advmask", "proposed", "our method", "system architecture",
    ],
    "experiments": [
        "experiment", "experiments", "experimental setup",
        "experimental design", "evaluation", "dataset", "datasets",
        "data set", "case study", "implementation",
        "expérimentation", "évaluation", "protocole expérimental",
        "experimental protocol", "materials",
    ],
    "results": [
        "results", "experimental results", "result analysis",
        "analysis", "performance analysis", "discussion",
        "résultats", "resultats", "analyse des résultats",
        "ablation studies", "ablation study", "comparison", "comparisons",
    ],
    "limitations": ["limitation", "limitations", "threats to validity", "limites"],
    "future_work": ["future work", "travaux futurs", "perspective", "perspectives"],
    "conclusion": [
        "conclusion", "conclusions", "conclusion and future work",
        "conclusion et perspectives", "conclusions and future directions",
    ],
    "references": ["references", "bibliography", "références", "bibliographie", "acknowledgments"],
    "toc": [
        "sommaire", "table des matières", "table of contents",
        "contents", "table des figures", "liste des tableaux",
        "list of figures", "list of tables",
    ],
}

GENERIC_ONE_WORD_TITLES = {
    "data", "model", "models", "method", "methods", "algorithm", "algorithms",
    "performance", "analysis", "results", "dataset", "datasets", "evaluation",
    "implementation", "approach", "framework", "background", "discussion",
}

MAIN_SECTION_RE = re.compile(
    r"^\s*(?P<num>(?:[IVXLC]+|\d+(?:\.\d+)*))\.??\s+(?P<title>[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9\-/&:,() ]{1,120})\s*$",
    flags=re.IGNORECASE,
)

CHAPTER_RE = re.compile(
    r"^\s*(?P<prefix>chapter|chapitre|section)\s+(?P<num>\d+(?:\.\d+)*)\.?\s*(?P<title>[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9\-/&:,() ]{1,140})?\s*$",
    flags=re.IGNORECASE,
)


def _normalize_heading_text(value: str) -> str:
    text = _safe_text(value).lower()
    text = text.replace("’", "'").replace("`", "'")
    text = _strip_accents(text)
    text = re.sub(r"[^a-z0-9\s\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _strip_heading_number(value: str) -> str:
    h = _normalize_heading_text(value)
    h = re.sub(r"^\s*(chapter|chapitre|section)\s+\d+(\.\d+)*\s*", "", h)
    h = re.sub(r"^\s*(\d+|[ivxlcdm]+)(\.\d+)*\.?\s+", "", h)
    return h.strip()


def _heading_to_category(heading: str, fallback_by_order: Optional[str] = None) -> Optional[str]:
    h = _strip_heading_number(heading)
    if not h:
        return None

    best_category = None
    best_len = 0

    for category, titles in SECTION_TITLE_MAP.items():
        for title in titles:
            t = _normalize_heading_text(title)
            if not t:
                continue

            if h == t or h.startswith(t + " ") or t in h:
                if len(t) > best_len:
                    best_category = category
                    best_len = len(t)

    if best_category:
        return best_category

    if fallback_by_order:
        return fallback_by_order

    return None


def _clean_heading_line(line: str) -> str:
    line = _safe_text(line, 180)
    line = re.sub(r"\.{3,}\s*\d{1,4}\s*$", "", line).strip()
    line = re.sub(r"\s+", " ", line).strip()
    return line


def _is_toc_line(line: str) -> bool:
    low = _normalize_heading_text(line)
    if not low:
        return False

    if low in {_normalize_heading_text(x) for x in SECTION_TITLE_MAP["toc"]}:
        return True

    if re.search(r"\.{3,}\s*\d{1,4}\s*$", line):
        return True

    return False


def _extract_toc_titles(lines: List[str]) -> List[str]:
    toc_titles: List[str] = []
    in_toc = False
    toc_started_at = -1

    for idx, raw in enumerate(lines[:250]):
        line = _clean_heading_line(raw)
        norm = _normalize_heading_text(line)

        if norm in {_normalize_heading_text(x) for x in SECTION_TITLE_MAP["toc"]}:
            in_toc = True
            toc_started_at = idx
            continue

        if not in_toc:
            continue

        if toc_started_at >= 0 and idx - toc_started_at > 120:
            break

        cleaned = re.sub(r"\.{2,}\s*\d{1,4}\s*$", "", line).strip()
        cleaned = re.sub(r"^\s*\d{1,4}\s*$", "", cleaned).strip()

        if not cleaned:
            continue

        if re.match(r"^(\d+(?:\.\d+)*|[IVXLC]+)\.?\s+[A-Za-zÀ-ÿ]", cleaned, flags=re.I):
            toc_titles.append(cleaned)
            continue

        if re.match(r"^(chapter|chapitre|section)\s+\d+", cleaned, flags=re.I):
            toc_titles.append(cleaned)
            continue

        if len(cleaned) < 90 and _heading_to_category(cleaned):
            toc_titles.append(cleaned)

    out: List[str] = []
    seen = set()

    for t in toc_titles:
        key = _normalize_heading_text(t)
        if key and key not in seen:
            seen.add(key)
            out.append(t)

    return out[:80]


def _is_strict_heading_candidate(
    line: str,
    prev_line: str = "",
    next_line: str = "",
    toc_whitelist: Optional[List[str]] = None,
) -> bool:
    raw = _clean_heading_line(line)
    if not raw:
        return False

    norm = _normalize_heading_text(raw)
    if not norm:
        return False

    if _is_toc_line(raw):
        return False

    if len(raw) > 160:
        return False

    if raw.count(".") >= 2 and not re.match(r"^\s*\d+(?:\.\d+)*", raw):
        return False

    if len(raw.split()) > 14:
        return False

    if norm in GENERIC_ONE_WORD_TITLES:
        has_number = bool(re.match(r"^\s*(\d+(?:\.\d+)*|[IVXLC]+)\.??\s+", raw, flags=re.I))
        is_upper = raw.isupper()
        isolated = (not prev_line.strip()) or (not next_line.strip())
        if not (has_number or (is_upper and isolated)):
            return False

    if toc_whitelist:
        raw_norm = _strip_heading_number(raw)
        for t in toc_whitelist:
            t_norm = _strip_heading_number(t)
            if raw_norm == t_norm or raw_norm.startswith(t_norm + " "):
                return True

    if MAIN_SECTION_RE.match(raw):
        return True

    if CHAPTER_RE.match(raw):
        return True

    letters = [c for c in raw if c.isalpha()]
    if letters:
        upper_ratio = sum(c.isupper() for c in letters) / max(1, len(letters))
        isolated = (not prev_line.strip()) or (not next_line.strip())
        if upper_ratio >= 0.75 and len(raw) <= 100 and isolated:
            return True

    if _heading_to_category(raw) in {"abstract", "conclusion", "references", "future_work", "limitations"}:
        isolated = (not prev_line.strip()) or (not next_line.strip())
        if isolated:
            return True

    return False


def _iter_logical_lines_with_pos(text: str) -> List[Dict[str, Any]]:
    raw_lines = text.splitlines()
    items: List[Dict[str, Any]] = []
    pos = 0
    idx = 0

    while idx < len(raw_lines):
        line = raw_lines[idx]
        start = pos
        next_pos = pos + len(line) + 1
        clean = _clean_heading_line(line)

        if re.fullmatch(r"[IVXLC]+\.?", clean, flags=re.I) and idx + 1 < len(raw_lines):
            nxt = _clean_heading_line(raw_lines[idx + 1])
            if nxt and len(nxt) <= 100:
                merged = f"{clean} {nxt}"
                items.append({"line": merged, "start": start, "raw_index": idx})
                pos = next_pos + len(raw_lines[idx + 1]) + 1
                idx += 2
                continue

        items.append({"line": clean, "start": start, "raw_index": idx})
        pos = next_pos
        idx += 1

    return items


def _fallback_category_by_order(title: str, seen_categories: List[str]) -> Optional[str]:
    t = _strip_heading_number(title)
    if not t:
        return None

    if "introduction" in seen_categories and "method" not in seen_categories:
        if "related_work" in seen_categories or len(seen_categories) >= 1:
            return "method"

    return None


def parse_article_sections_by_headings(full_text: str) -> Dict[str, Any]:
    text = full_text or ""
    logical = _iter_logical_lines_with_pos(text)
    raw_lines = [x["line"] for x in logical]
    toc_titles = _extract_toc_titles(raw_lines)

    headings: List[Dict[str, Any]] = []
    seen_categories: List[str] = []

    for i, item in enumerate(logical):
        line = item["line"]
        prev_line = logical[i - 1]["line"] if i > 0 else ""
        next_line = logical[i + 1]["line"] if i + 1 < len(logical) else ""

        if not _is_strict_heading_candidate(line, prev_line, next_line, toc_titles):
            continue

        fallback = _fallback_category_by_order(line, seen_categories)
        category = _heading_to_category(line, fallback_by_order=fallback)

        if not category or category == "toc":
            continue

        if category == "references":
            headings.append({
                "heading": line,
                "category": category,
                "start": item["start"],
                "end": item["start"] + len(line),
            })
            break

        headings.append({
            "heading": line,
            "category": category,
            "start": item["start"],
            "end": item["start"] + len(line),
        })

        if category not in seen_categories:
            seen_categories.append(category)

    sections = {
        "abstract": "",
        "introduction": "",
        "related_work": "",
        "method": "",
        "experiments": "",
        "results": "",
        "limitations": "",
        "future_work": "",
        "conclusion": "",
    }

    detected: List[Dict[str, Any]] = []

    if not headings:
        return {
            "sections": sections,
            "detected_headings": [],
            "headings_count": 0,
            "toc_titles": toc_titles,
        }

    for i, h in enumerate(headings):
        category = h["category"]

        if category == "references":
            break

        start = h["end"]
        end = headings[i + 1]["start"] if i + 1 < len(headings) else len(text)
        content = _safe_text(text[start:end], MAX_SECTION_CHARS)

        if len(content) < 40:
            continue

        if category in sections:
            if sections[category]:
                sections[category] += "\n\n" + content
            else:
                sections[category] = content

            detected.append({
                "heading": h["heading"],
                "category": category,
                "chars": len(content),
                "start": h["start"],
            })

    return {
        "sections": sections,
        "detected_headings": detected,
        "headings_count": len(detected),
        "toc_titles": toc_titles,
    }


def extract_sections(full_text: str, abstract_hint: str = "") -> Dict[str, str]:
    parsed = parse_article_sections_by_headings(full_text)
    sections = parsed["sections"]

    if not sections.get("abstract"):
        sections["abstract"] = abstract_hint or _safe_text(full_text, 2500)

    return sections


def _extract_abstract(article: Article, full_text: str) -> str:
    from_json = _extract_abstract_from_json(article)
    if from_json:
        return _safe_text(from_json, 4000)

    parsed = parse_article_sections_by_headings(full_text)
    sections = parsed.get("sections", {})

    if sections.get("abstract"):
        return _limit_sentences(sections["abstract"], max_sentences=6, max_chars=3500)

    m = re.search(
        r"(?is)abstract\s*[—:-]?\s*(.*?)(?:\n\s*(?:I\.|1\.?|INTRODUCTION|Introduction)\s+)",
        full_text or "",
    )
    if m:
        return _limit_sentences(m.group(1), max_sentences=6, max_chars=3500)

    return _safe_text(full_text, 1800)


# ============================================================
# Thesis-aware section parsing
# ============================================================

def detect_document_type(full_text: str, parsed: Optional[Dict[str, Any]] = None) -> str:
    text = full_text or ""
    head = text[:50000].lower()
    parsed = parsed or {}
    headings_count = int(parsed.get("headings_count") or 0)

    thesis_signals = 0

    for pat in [
        r"\bth[eè]se\b",
        r"\bthesis\b",
        r"\bdoctorat\b",
        r"\bdoctoral\b",
        r"\bdissertation\b",
        r"\bchapitre\s+\d+\b",
        r"\bchapter\s+\d+\b",
        r"\bgeneral introduction\b",
        r"\bconclusion and perspective\b",
        r"\blist of figures\b",
        r"\blist of tables\b",
        r"\btable des mati[eè]res\b",
        r"\btable of contents\b",
    ]:
        if re.search(pat, head, flags=re.I):
            thesis_signals += 1

    if len(text) > 120000 and thesis_signals >= 2:
        return "thesis"

    if headings_count >= 35 and thesis_signals >= 1:
        return "thesis"

    if re.search(r"(?im)^\s*CHAPITRE\s+\d+", text) or re.search(r"(?im)^\s*CHAPTER\s+\d+", text):
        if len(text) > 60000:
            return "thesis"

    return "article"


def _find_thesis_body_start(text: str) -> int:
    candidates: List[int] = []

    for pat in [
        r"(?im)^\s*GENERAL\s+INTRODUCTION\s*$",
        r"(?im)^\s*INTRODUCTION\s+G[ÉE]N[ÉE]RALE\s*$",
        r"(?im)^\s*CHAPITRE\s+1\b",
        r"(?im)^\s*CHAPTER\s+1\b",
        r"(?im)^\s*1\.\s+INTRODUCTION\s*$",
        r"(?im)^\s*1\.1\s+INTRODUCTION\s*$",
    ]:
        m = re.search(pat, text or "")
        if m:
            candidates.append(m.start())

    return min(candidates) if candidates else 0


def _is_bad_thesis_heading_line(line: str) -> bool:
    raw = _clean_heading_line(line)

    if not raw:
        return True

    if "...." in raw or re.search(r"\.{3,}\s*\d+\s*$", raw):
        return True

    if re.match(r"^\d{1,4}\s*(CHAPITRE|CHAPTER)\b", raw, flags=re.I):
        return True

    if len(raw) > 170 or len(raw.split()) > 18:
        return True

    return False


def _thesis_heading_category(line: str) -> Optional[str]:
    raw = _clean_heading_line(line)
    norm = _normalize_heading_text(raw)

    if not norm:
        return None

    if re.search(r"\b(references|bibliograph|acknowledg|remerciements|appendix|annexe)\b", norm):
        return "references"

    if re.search(r"\b(table of contents|table des matieres|list of figures|list of tables|liste des figures|liste des tableaux)\b", norm):
        return "toc"

    if re.search(r"\b(general introduction|introduction generale)\b", norm):
        return "introduction"

    if re.search(r"\b(state of the art|literature review|related work|etat de l art|background)\b", norm):
        return "related_work"

    if re.search(r"\b(method|methodology|methods|proposed method|materials|dataset|datasets|feature selection|mrmr|mutual information|algorithm|approach|model|adaptive|partition|ga-like|augmentation|classification)\b", norm):
        return "method"

    if re.search(r"\b(experiment|experimental|evaluation|results|analysis|discussion|benchmark|performance)\b", norm):
        return "results"

    if re.search(r"\b(limitations|limits|threats to validity|drawbacks|limites)\b", norm):
        return "limitations"

    if re.search(r"\b(conclusion and perspective|conclusion et perspective|perspective|future work|future research|perspectives)\b", norm):
        return "future_work"

    if re.search(r"\b(conclusion|conclusions)\b", norm):
        return "conclusion"

    if re.match(r"^(chapitre|chapter)\s+\d+", norm):
        return "related_work"

    return None


def _is_thesis_heading_candidate(line: str) -> bool:
    raw = _clean_heading_line(line)

    if _is_bad_thesis_heading_line(raw):
        return False

    if re.match(r"^(CHAPITRE|CHAPTER)\s+\d+\b", raw, flags=re.I):
        return True

    if re.match(r"^\d+(?:\.\d+){1,3}\s+[A-Z][A-Za-z0-9,;:()\- /]+$", raw):
        cat = _thesis_heading_category(raw)
        return cat is not None

    if raw.isupper() and len(raw) <= 120:
        cat = _thesis_heading_category(raw)
        return cat is not None

    if _thesis_heading_category(raw) in {"conclusion", "future_work", "limitations"}:
        return True

    return False


def parse_thesis_sections(full_text: str, abstract_hint: str = "") -> Dict[str, Any]:
    text = full_text or ""
    body_start = _find_thesis_body_start(text)
    logical = _iter_logical_lines_with_pos(text)

    headings: List[Dict[str, Any]] = []

    for item in logical:
        start = int(item.get("start") or 0)

        if start < body_start:
            continue

        line = item.get("line") or ""

        if not _is_thesis_heading_candidate(line):
            continue

        cat = _thesis_heading_category(line)

        if not cat or cat == "toc":
            continue

        headings.append({
            "heading": line,
            "category": cat,
            "start": start,
            "end": start + len(line),
        })

        if cat == "references":
            break

    sections = {
        "abstract": abstract_hint or "",
        "introduction": "",
        "related_work": "",
        "method": "",
        "experiments": "",
        "results": "",
        "limitations": "",
        "future_work": "",
        "conclusion": "",
    }

    detected: List[Dict[str, Any]] = []

    if not headings:
        fallback = parse_article_sections_by_headings(text)
        fallback["document_type"] = "thesis_fallback_article_parser"
        return fallback

    high_value_categories = {"method", "experiments", "results", "limitations", "future_work", "conclusion"}

    for i, h in enumerate(headings):
        cat = h["category"]

        if cat == "references":
            break

        start = h["end"]
        end = headings[i + 1]["start"] if i + 1 < len(headings) else len(text)
        raw_content = _safe_text(text[start:end])

        if len(raw_content) < 60:
            continue

        max_chars = MAX_SECTION_CHARS

        if cat == "related_work":
            max_chars = min(MAX_SECTION_CHARS, 1800)
        elif cat in high_value_categories:
            max_chars = min(MAX_SECTION_CHARS * 2, 9000)

        content = _safe_text(raw_content, max_chars)
        target_cat = cat

        if target_cat in sections:
            if sections[target_cat]:
                sections[target_cat] += "\n\n" + content
            else:
                sections[target_cat] = content

        detected.append({
            "heading": h["heading"],
            "category": target_cat,
            "chars": len(content),
            "start": h["start"],
        })

    if not sections.get("future_work"):
        m = re.search(
            r"(?is)(?:5\.2\s*/?\s*PERSPECTIVE|PERSPECTIVE|PERSPECTIVES)(.*?)(?:REFERENCES|BIBLIOGRAPHY|$)",
            text[body_start:],
        )
        if m:
            sections["future_work"] = _safe_text(m.group(1), min(MAX_SECTION_CHARS, 4500))

    if not sections.get("conclusion"):
        matches = list(re.finditer(r"(?im)^\s*(?:CHAPITRE\s+\d+\.\s*)?CONCLUSION(?:S)?\s*$", text[body_start:]))
        if matches:
            m = matches[-1]
            start = body_start + m.end()
            end = min(len(text), start + MAX_SECTION_CHARS)
            sections["conclusion"] = _safe_text(text[start:end], MAX_SECTION_CHARS)

    return {
        "sections": sections,
        "detected_headings": detected,
        "headings_count": len(detected),
        "toc_titles": [],
        "document_type": "thesis",
        "thesis_body_start": body_start,
    }


def parse_document_sections(full_text: str, abstract_hint: str = "") -> Dict[str, Any]:
    article_parsed = parse_article_sections_by_headings(full_text)
    doc_type = detect_document_type(full_text, article_parsed)

    if doc_type == "thesis":
        parsed = parse_thesis_sections(full_text, abstract_hint=abstract_hint)
        parsed["document_type"] = "thesis"
        return parsed

    article_parsed["document_type"] = "article"
    return article_parsed


def extract_sections_smart(full_text: str, abstract_hint: str = "") -> Dict[str, str]:
    parsed = parse_document_sections(full_text, abstract_hint=abstract_hint)
    sections = parsed.get("sections", {})

    if not sections.get("abstract"):
        sections["abstract"] = abstract_hint or _safe_text(full_text, 2500)

    return sections


# ============================================================
# Evidence extraction
# ============================================================

LIMITATION_SIGNAL_PATTERNS = [
    r"\blimitation(s)?\b",
    r"\blimited\b",
    r"\blimit(ed|s|ing)?\b",
    r"\bconstraint(s)?\b",
    r"\bdrawback(s)?\b",
    r"\bweakness(es)?\b",
    r"\bshortcoming(s)?\b",
    r"\bthreat(s)? to validity\b",
    r"\bevaluated only\b",
    r"\btested only\b",
    r"\bonly evaluated\b",
    r"\bonly tested\b",
    r"\brestricted to\b",
    r"\bnot evaluated\b",
    r"\bnot validated\b",
    r"\bcannot\b",
    r"\bcan not\b",
    r"\bfails?\b",
    r"\bchallenge(s)?\b",
    r"\blimite(s)?\b",
]

FUTURE_SIGNAL_PATTERNS = [
    r"\bfuture work\b",
    r"\bfuture research\b",
    r"\bin future\b",
    r"\bwe will\b",
    r"\bwill be applied\b",
    r"\bwill investigate\b",
    r"\bwill focus\b",
    r"\bnext step(s)?\b",
    r"\bperspective(s)?\b",
    r"\btravaux futurs\b",
    r"\btravail futur\b",
]

EXPLICIT_LIMITATION_PATTERNS = [
    r"\blimitation(s)?\b",
    r"\bone limitation\b",
    r"\bmain limitation\b",
    r"\bthreat(s)? to validity\b",
    r"\bdrawback(s)?\b",
    r"\bweakness(es)?\b",
    r"\bshortcoming(s)?\b",
    r"\blimite(s)?\b",
]

METHODOLOGICAL_CONSTRAINT_PATTERNS = [
    r"\bhyperparameter(s)?\b",
    r"\bparameter(s)?\b",
    r"\bparameter setting(s)?\b",
    r"\bsetting(s)?\b",
    r"\bthreshold(s)?\b",
    r"\bwhite[- ]box\b",
    r"\bpre[- ]trained\b",
    r"\btrained classification model\b",
    r"\battack target\b",
    r"\btarget model\b",
    r"\bfirst use\b",
    r"\bwe first use\b",
    r"\brequire(s|d)?\b",
    r"\bneed(s|ed)?\b",
    r"\bdepend(s|ed)? on\b",
    r"\bassume(s|d)?\b",
    r"\bsensitive to\b",
    r"\bbalance between\b",
    r"\btrade[- ]off\b",
    r"\btoo small\b",
    r"\btoo large\b",
    r"\bnot too large\b",
    r"\bnot too small\b",
    r"\bdegrade(s|d)?\b",
    r"\bdegrad(e|es|ed|ing)\b",
    r"\bdamage(s|d)?\b",
    r"\bside effect\b",
    r"\bnegative impact\b",
    r"\bdrop(s|ped)? sharply\b",
    r"\blower accuracy\b",
    r"\bperformance drops?\b",
    r"\baccuracy drops?\b",
    r"\bcomputational cost\b",
    r"\bcomputation cost\b",
    r"\bmemory cost\b",
    r"\btime[- ]consuming\b",
]

PROJECT_GAP_SIGNAL_PATTERNS = [
    r"\bclassification task(s)?\b",
    r"\bimage classification\b",
    r"\bobject detection\b",
    r"\bsegmentation\b",
    r"\bbenchmark(s)?\b",
    r"\bdataset(s)?\b",
    r"\bevaluation protocol(s)?\b",
    r"\breference data(base|set)?\b",
    r"\bapplication domain\b",
    r"\bexperimental result(s)?\b",
    r"\bexperiment(s)? include\b",
    r"\bwe conduct experiments on\b",
    r"\bwe evaluate\b",
    r"\bvalidation set\b",
    r"\btest set\b",
    r"\bfuture work\b",
    r"\bfuture research\b",
    r"\breal[- ]world\b",
    r"\bindustrial\b",
    r"\bdeployment\b",
]

HIGH_VALUE_GLOBAL_PATTERNS = [
    r"\bhyperparameter(s)?\b",
    r"\bparameter(s)?\b",
    r"\btoo small\b",
    r"\btoo large\b",
    r"\baccuracy drops?\b",
    r"\bperformance drops?\b",
    r"\bdegrade(s|d)?\b",
    r"\bnegative impact\b",
    r"\btrained classification model\b",
    r"\battack target\b",
    r"\bpre[- ]trained\b",
    r"\bonly evaluated\b",
    r"\bonly tested\b",
    r"\bnot evaluated\b",
    r"\bfuture work\b",
    r"\bfuture research\b",
    r"\bAdvMask\b",
    r"\bmask\b",
    r"\bsparse\b",
    r"\baugmentation\b",
    r"\bmutual information\b",
    r"\bdimensionality reduction\b",
]

GENERIC_NOISE_PATTERNS = [
    r"^\s*\d+(\.\d+)*\s*$",
    r"^\s*table\s+[ivxlcdm0-9]+\b",
    r"^\s*fig\.\s*\d+\b",
    r"acknowledg",
    r"references",
    r"copyright",
    r"arxiv:",
    r"proceedings of",
]


def _matches_any_pattern(text: str, patterns: List[str]) -> bool:
    low = text.lower()
    return any(re.search(p, low, flags=re.IGNORECASE) for p in patterns)


def _sentence_split_for_evidence(text: str) -> List[str]:
    text = clean_article_text(text or "")

    if not text:
        return []

    text = re.sub(r"(?<![.!?:;])\n(?!\s*[A-Z][A-Z\s]{3,}$)", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-Ÿ0-9])", text)
    cleaned: List[str] = []

    for p in parts:
        p = clean_article_text(_safe_text(p, 1200)).strip()

        if not (45 <= len(p) <= 1200):
            continue

        low = p.lower()

        if any(re.search(x, low, flags=re.IGNORECASE) for x in GENERIC_NOISE_PATTERNS):
            continue

        digit_ratio = sum(ch.isdigit() for ch in p) / max(1, len(p))
        if digit_ratio > 0.22 and not _matches_any_pattern(p, HIGH_VALUE_GLOBAL_PATTERNS):
            continue

        cleaned.append(p)

    return cleaned


def _unique_dict_items(items: List[Dict[str, Any]], limit: int = 8) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()

    for item in items:
        text = item.get("text", "")
        key = re.sub(r"\W+", " ", text.lower()).strip()[:260]

        if not key or key in seen:
            continue

        seen.add(key)
        out.append(item)

        if len(out) >= limit:
            break

    return out


def _evidence_item(section_name: str, priority: int, sent: str, evidence_type: str) -> Dict[str, Any]:
    return {
        "section": section_name,
        "priority": priority,
        "type": evidence_type,
        "text": clean_article_text(sent),
    }


def _score_evidence_sentence(sent: str, evidence_type: str, priority: int) -> int:
    score = max(0, 20 - priority * 3)
    low = sent.lower()

    high_value_terms = [
        "future work", "future research", "not evaluated", "not validated",
        "only evaluated", "only tested", "trained classification model",
        "attack target", "pre-trained", "hyperparameter", "parameter",
        "too small", "too large", "accuracy drops", "performance drops",
        "negative impact", "degrade", "requires", "depends on",
        "real-world", "industrial", "deployment", "advmask", "sparse",
        "augmentation", "mutual information", "dimensionality reduction",
    ]

    for term in high_value_terms:
        if term in low:
            score += 6

    if evidence_type == "explicit_limitation":
        score += 10
    elif evidence_type == "future_work":
        score += 8
    elif evidence_type == "methodological_constraint":
        score += 6
    elif evidence_type == "project_gap_signal":
        score += 4

    if len(sent) > 900:
        score -= 4

    if sum(ch.isdigit() for ch in sent) / max(1, len(sent)) > 0.18:
        score -= 3

    if "table" in low and "parameter" not in low:
        score -= 4

    return score


def _add_evidence(
    target: List[Dict[str, Any]],
    section_name: str,
    priority: int,
    sent: str,
    evidence_type: str,
) -> None:
    item = _evidence_item(section_name, priority, sent, evidence_type)
    item["score"] = _score_evidence_sentence(sent, evidence_type, priority)
    target.append(item)


def _collect_evidence_from_block(
    section_name: str,
    block: str,
    priority: int,
    explicit: List[Dict[str, Any]],
    implicit: List[Dict[str, Any]],
    future: List[Dict[str, Any]],
    methodological: List[Dict[str, Any]],
    project_gap: List[Dict[str, Any]],
) -> None:
    for sent in _sentence_split_for_evidence(block):
        if _matches_any_pattern(sent, FUTURE_SIGNAL_PATTERNS):
            _add_evidence(future, section_name, priority, sent, "future_work")
            _add_evidence(project_gap, section_name, priority, sent, "future_work_as_gap")

        if _matches_any_pattern(sent, EXPLICIT_LIMITATION_PATTERNS):
            _add_evidence(explicit, section_name, priority, sent, "explicit_limitation")
        elif _matches_any_pattern(sent, LIMITATION_SIGNAL_PATTERNS):
            _add_evidence(implicit, section_name, priority, sent, "implicit_limitation")

        if _matches_any_pattern(sent, METHODOLOGICAL_CONSTRAINT_PATTERNS):
            _add_evidence(methodological, section_name, priority, sent, "methodological_constraint")

        if _matches_any_pattern(sent, PROJECT_GAP_SIGNAL_PATTERNS):
            _add_evidence(project_gap, section_name, priority, sent, "project_gap_signal")


def extract_limitation_evidence(sections: Dict[str, str], full_text: str) -> Dict[str, Any]:
    prioritized_blocks = [
        ("limitations", sections.get("limitations", ""), 1),
        ("discussion", sections.get("discussion", ""), 1),
        ("future_work", sections.get("future_work", ""), 2),
        ("conclusion", sections.get("conclusion", ""), 2),
        ("method", sections.get("method", ""), 3),
        ("experiments", sections.get("experiments", ""), 3),
        ("results", sections.get("results", ""), 3),
        ("abstract", sections.get("abstract", ""), 4),
    ]

    explicit: List[Dict[str, Any]] = []
    implicit: List[Dict[str, Any]] = []
    future: List[Dict[str, Any]] = []
    methodological: List[Dict[str, Any]] = []
    project_gap: List[Dict[str, Any]] = []

    for section_name, block, priority in prioritized_blocks:
        if block:
            _collect_evidence_from_block(
                section_name, block, priority,
                explicit, implicit, future, methodological, project_gap,
            )

    for sent in _sentence_split_for_evidence(full_text):
        if not _matches_any_pattern(sent, HIGH_VALUE_GLOBAL_PATTERNS):
            continue

        _collect_evidence_from_block(
            "full_text_high_value", sent, 5,
            explicit, implicit, future, methodological, project_gap,
        )

    def sort_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(items, key=lambda x: (-int(x.get("score", 0)), int(x.get("priority", 9))))

    explicit = sort_items(explicit)
    implicit = sort_items(implicit)
    future = sort_items(future)
    methodological = sort_items(methodological)
    project_gap = sort_items(project_gap)

    def strip_scores(items: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        unique = _unique_dict_items(items, limit=limit)
        for item in unique:
            item.pop("score", None)
        return unique

    return {
        "explicit_limitations": strip_scores(explicit, 8),
        "implicit_limitations": strip_scores(implicit, 8),
        "methodological_constraints": strip_scores(methodological, 12),
        "project_gap_evidence": strip_scores(project_gap, 12),
        "future_work": strip_scores(future, 8),
        "has_evidence": bool(explicit or implicit or future or methodological or project_gap),
    }


def _limitation_evidence_to_text(evidence: Dict[str, Any], max_chars: int = 10000) -> str:
    lines: List[str] = []

    for label, key in [
        ("LIMITES EXPLICITES", "explicit_limitations"),
        ("LIMITES IMPLICITES / SIGNAUX DE LIMITES", "implicit_limitations"),
        ("CONTRAINTES METHODOLOGIQUES", "methodological_constraints"),
        ("INDICES DE GAP POUR LE PROJET", "project_gap_evidence"),
        ("TRAVAUX FUTURS / PERSPECTIVES", "future_work"),
    ]:
        items = evidence.get(key) or []
        lines.append(f"\n{label}:")

        if not items:
            lines.append("- Aucun extrait trouvé.")
        else:
            for item in items:
                lines.append(f"- [{item.get('section')}] {item.get('text')}")

    return _safe_text("\n".join(lines), max_chars)


# ============================================================
# LLM utilities
# ============================================================

def _extract_json_from_llm(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None

    text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)

    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return None

    return None


def _call_ollama_json(prompt: str, task: str = "ollama") -> Optional[Dict[str, Any]]:
    if EXTRACTIVE_ONLY_PHASE2 or DISABLE_LLM or LLM_STRATEGY in {"off", "none", "template", "extractive", "no_llm", "paragraphs", "evidence_only"}:
        print(f"[EnnoScholar][ArticleCards][LLM] SKIP task={task} extractive_only={EXTRACTIVE_ONLY_PHASE2} disable_llm={DISABLE_LLM} strategy={LLM_STRATEGY}", flush=True)
        return None

    started = time.time()
    prompt_chars = len(prompt or "")
    print(
        f"[EnnoScholar][ArticleCards][LLM] START task={task} "
        f"model={OLLAMA_MODEL} prompt_chars={prompt_chars} timeout={LLM_TIMEOUT}s",
        flush=True,
    )

    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL.rstrip('/')}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": 0.02,
                    "num_ctx": OLLAMA_NUM_CTX,
                    "num_predict": OLLAMA_NUM_PREDICT,
                },
            },
            timeout=LLM_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        parsed = _extract_json_from_llm(data.get("response", ""))
        elapsed = round(time.time() - started, 2)
        print(
            f"[EnnoScholar][ArticleCards][LLM] END task={task} ok={bool(parsed)} elapsed={elapsed}s",
            flush=True,
        )
        return parsed
    except Exception as exc:
        elapsed = round(time.time() - started, 2)
        print(
            f"[EnnoScholar][ArticleCards][LLM] ERROR task={task} elapsed={elapsed}s error={exc}",
            flush=True,
        )
        return None


# ============================================================
# Limitation analysis layer
# ============================================================

def _as_analysis_item(text: str, source: str, evidence_type: str, proof: str = "") -> Dict[str, str]:
    item = {
        "texte": clean_article_text(_safe_text(text, 600)),
        "source": source,
        "type": evidence_type,
    }

    if proof:
        item["preuve"] = clean_article_text(_safe_text(proof, 450))

    return item


def _template_limitations_analysis(evidence: Dict[str, Any]) -> Dict[str, Any]:
    explicit = evidence.get("explicit_limitations") or []
    methodological = evidence.get("methodological_constraints") or []
    project_gap = evidence.get("project_gap_evidence") or []
    future = evidence.get("future_work") or []

    limitations_explicites = [
        _as_analysis_item(x["text"], x.get("section", "unknown"), "limite_explicite", x["text"])
        for x in explicit[:4]
    ]

    contraintes_methodologiques = [
        _as_analysis_item(x["text"], x.get("section", "unknown"), "contrainte_methodologique", x["text"])
        for x in methodological[:6]
    ]

    gap_pour_le_projet = [
        _as_analysis_item(x["text"], x.get("section", "unknown"), "gap_transposition", x["text"])
        for x in project_gap[:6]
    ]

    if not gap_pour_le_projet and future:
        gap_pour_le_projet = [
            _as_analysis_item(x["text"], x.get("section", "unknown"), "extension_non_validee", x["text"])
            for x in future[:3]
        ]

    travaux_futurs_items = [
        _as_analysis_item(x["text"], x.get("section", "unknown"), "travail_futur", x["text"])
        for x in future[:4]
    ]

    limites = (
        " ".join(x["texte"] for x in limitations_explicites[:2])
        if limitations_explicites
        else "Les auteurs ne formulent pas de limitation explicite dans les sections analysées."
    )

    contraintes_resume = (
        " ".join(x["texte"] for x in contraintes_methodologiques[:2])
        if contraintes_methodologiques
        else "Aucune contrainte méthodologique clairement exploitable n'a été extraite."
    )

    gap_resume = (
        " ".join(x["texte"] for x in gap_pour_le_projet[:2])
        if gap_pour_le_projet
        else "Aucun gap de transposition clairement exploitable n'a été extrait."
    )

    travaux_futurs = (
        " ".join(x["texte"] for x in travaux_futurs_items[:2])
        if travaux_futurs_items
        else "Non explicitement indiqué dans le texte extrait."
    )

    return {
        "limitations_analysis": {
            "limitations_explicites": limitations_explicites,
            "contraintes_methodologiques": contraintes_methodologiques,
            "gap_pour_le_projet": gap_pour_le_projet,
            "travaux_futurs": travaux_futurs_items,
        },
        "limites": _limit_sentences(limites, 3, 900),
        "contraintes_methodologiques_resume": _limit_sentences(contraintes_resume, 3, 900),
        "gap_pour_notre_projet_resume": _limit_sentences(gap_resume, 3, 900),
        "travaux_futurs": _limit_sentences(travaux_futurs, 3, 900),
        "limite_pour_notre_projet": _limit_sentences(gap_resume, 3, 900),
        "limitation_source": "template_evidence",
    }


def summarize_limitations_with_llm(
    article: Article,
    authors: List[str],
    limitation_evidence: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not limitation_evidence.get("has_evidence"):
        return {
            "limitations_analysis": {
                "limitations_explicites": [],
                "contraintes_methodologiques": [],
                "gap_pour_le_projet": [],
                "travaux_futurs": [],
            },
            "limites": "Les auteurs ne formulent pas de limitation explicite dans les sections analysées.",
            "contraintes_methodologiques_resume": "Aucune contrainte méthodologique clairement exploitable n'a été extraite.",
            "gap_pour_notre_projet_resume": "Aucun gap de transposition clairement exploitable n'a été extrait.",
            "travaux_futurs": "Non explicitement indiqué dans le texte extrait.",
            "limite_pour_notre_projet": "Aucun gap de transposition clairement exploitable n'a été extrait.",
            "limitation_source": "no_evidence_found",
        }

    evidence_text = _limitation_evidence_to_text(limitation_evidence)
    author_label = _author_label(authors)
    year = article.year or "s.d."

    prompt = f"""
Tu es un assistant scientifique spécialisé dans les états de l'art CIR.

Ta mission : analyser uniquement les extraits fournis et distinguer 4 éléments.

Éléments à distinguer :
1. limitations_explicites : uniquement les limites formulées clairement par les auteurs.
2. contraintes_methodologiques : contraintes directement déduites de la méthode ou du protocole, sans extrapoler.
3. gap_pour_le_projet : ce que l'article ne démontre pas complètement pour une transposition projet/CIR.
4. travaux_futurs : perspectives explicitement mentionnées.

Règles strictes :
- Réponds uniquement en JSON valide.
- N'invente jamais.
- Utilise seulement les extraits fournis.
- Chaque item doit contenir : texte, preuve, source.
- Si aucune limite explicite n'est présente, laisse limitations_explicites vide.
- Ne dis pas coût de calcul élevé sauf si l'extrait le dit explicitement.
- Ne transforme pas une perspective en limite explicite ; mets-la dans gap_pour_le_projet ou travaux_futurs.
- Priorise les contraintes utiles CIR : prérequis, modèle pré-entraîné, dépendance à un réglage, hyperparamètres, seuils, données limitées, validation uniquement sur certains datasets/tâches, baisse de performance si mauvais réglage.

JSON attendu exactement :
{{
  "limitations_analysis": {{
    "limitations_explicites": [{{"texte": "...", "preuve": "...", "source": "explicit"}}],
    "contraintes_methodologiques": [{{"texte": "...", "preuve": "...", "source": "method"}}],
    "gap_pour_le_projet": [{{"texte": "...", "preuve": "...", "source": "gap"}}],
    "travaux_futurs": [{{"texte": "...", "preuve": "...", "source": "future_work"}}]
  }},
  "limites": "...",
  "contraintes_methodologiques_resume": "...",
  "gap_pour_notre_projet_resume": "...",
  "travaux_futurs": "...",
  "limite_pour_notre_projet": "...",
  "limitation_source": "explicit|implicit|methodological|future_work|gap|no_evidence_found"
}}

ARTICLE : {article.title}
AUTEUR_ANNEE : {author_label} ({year})

EXTRAITS CANDIDATS :
{evidence_text}
""".strip()

    result = _call_ollama_json(prompt, task=f"limitations_article_{article.id}")

    if not result:
        return None

    analysis = result.get("limitations_analysis")
    if not isinstance(analysis, dict):
        analysis = {}

    def clean_items(items: Any, max_items: int = 6) -> List[Dict[str, str]]:
        if not isinstance(items, list):
            return []

        out: List[Dict[str, str]] = []

        for item in items[:max_items]:
            if isinstance(item, str):
                out.append({
                    "texte": clean_article_text(_safe_text(item, 600)),
                    "preuve": "",
                    "source": "llm",
                })
            elif isinstance(item, dict):
                out.append({
                    "texte": clean_article_text(_safe_text(item.get("texte"), 600)),
                    "preuve": clean_article_text(_safe_text(item.get("preuve"), 450)),
                    "source": _safe_text(item.get("source"), 80) or "llm",
                })

        return [x for x in out if x.get("texte")]

    clean_analysis = {
        "limitations_explicites": clean_items(analysis.get("limitations_explicites"), 5),
        "contraintes_methodologiques": clean_items(analysis.get("contraintes_methodologiques"), 6),
        "gap_pour_le_projet": clean_items(analysis.get("gap_pour_le_projet"), 6),
        "travaux_futurs": clean_items(analysis.get("travaux_futurs"), 5),
    }

    if not clean_analysis["contraintes_methodologiques"] and limitation_evidence.get("methodological_constraints"):
        clean_analysis["contraintes_methodologiques"] = [
            {
                "texte": _safe_text(x.get("text"), 600),
                "preuve": _safe_text(x.get("text"), 450),
                "source": x.get("section", "method"),
            }
            for x in limitation_evidence.get("methodological_constraints", [])[:3]
        ]

    if not clean_analysis["gap_pour_le_projet"] and limitation_evidence.get("project_gap_evidence"):
        clean_analysis["gap_pour_le_projet"] = [
            {
                "texte": _safe_text(x.get("text"), 600),
                "preuve": _safe_text(x.get("text"), 450),
                "source": x.get("section", "gap"),
            }
            for x in limitation_evidence.get("project_gap_evidence", [])[:3]
        ]

    clean = {
        "limitations_analysis": clean_analysis,
        "limites": clean_article_text(_safe_text(result.get("limites"), 1200)),
        "contraintes_methodologiques_resume": clean_article_text(_safe_text(result.get("contraintes_methodologiques_resume"), 1200)),
        "gap_pour_notre_projet_resume": clean_article_text(_safe_text(result.get("gap_pour_notre_projet_resume"), 1200)),
        "travaux_futurs": clean_article_text(_safe_text(result.get("travaux_futurs"), 900)),
        "limite_pour_notre_projet": clean_article_text(_safe_text(result.get("limite_pour_notre_projet"), 1200)),
        "limitation_source": _safe_text(result.get("limitation_source"), 100) or "unknown",
    }

    if not clean["limites"]:
        clean["limites"] = "Les auteurs ne formulent pas de limitation explicite dans les sections analysées."

    if not clean["contraintes_methodologiques_resume"]:
        c = clean_analysis.get("contraintes_methodologiques", [])
        clean["contraintes_methodologiques_resume"] = (
            _limit_sentences(" ".join(x.get("texte", "") for x in c[:2]), 3, 900)
            or "Aucune contrainte méthodologique clairement exploitable n'a été extraite."
        )

    if not clean["gap_pour_notre_projet_resume"]:
        g = clean_analysis.get("gap_pour_le_projet", [])
        clean["gap_pour_notre_projet_resume"] = (
            _limit_sentences(" ".join(x.get("texte", "") for x in g[:2]), 3, 900)
            or "Aucun gap de transposition clairement exploitable n'a été extrait."
        )

    if not clean["limite_pour_notre_projet"]:
        clean["limite_pour_notre_projet"] = clean["gap_pour_notre_projet_resume"]

    return clean


def apply_limitation_layer(
    card: Dict[str, Any],
    article: Article,
    authors: List[str],
    sections: Dict[str, str],
    full_text: str,
    use_llm: bool = True,
) -> Dict[str, Any]:
    evidence = extract_limitation_evidence(sections, full_text)
    summary = summarize_limitations_with_llm(article, authors, evidence) if use_llm else None

    if not summary:
        summary = _template_limitations_analysis(evidence)

    analysis = summary.get("limitations_analysis") or {
        "limitations_explicites": [],
        "contraintes_methodologiques": [],
        "gap_pour_le_projet": [],
        "travaux_futurs": [],
    }

    card["limitations_analysis"] = analysis
    card["contraintes_methodologiques"] = analysis.get("contraintes_methodologiques", [])
    card["gap_pour_notre_projet"] = analysis.get("gap_pour_le_projet", [])

    for key in [
        "limites",
        "travaux_futurs",
        "limite_pour_notre_projet",
        "contraintes_methodologiques_resume",
        "gap_pour_notre_projet_resume",
    ]:
        if summary.get(key):
            card[key] = summary[key]

    if not card.get("limites"):
        card["limites"] = "Les auteurs ne formulent pas de limitation explicite dans les sections analysées."

    if not card.get("limite_pour_notre_projet"):
        card["limite_pour_notre_projet"] = card.get("gap_pour_notre_projet_resume") or card.get("limites")

    card.setdefault("evidence", {})["limitation_evidence"] = evidence
    card.setdefault("evidence", {})["limitation_source"] = summary.get("limitation_source")

    return card


# ============================================================
# NEW — Technical method / concept layer
# ============================================================

TECHNICAL_METHOD_SIGNAL_PATTERNS = [
    r"\bwe propose\b",
    r"\bwe present\b",
    r"\bwe introduce\b",
    r"\bproposed method\b",
    r"\bproposed approach\b",
    r"\bour method\b",
    r"\bour approach\b",
    r"\bframework\b",
    r"\balgorithm\b",
    r"\bmodel\b",
    r"\bmask\b",
    r"\badversarial\b",
    r"\bsparse\b",
    r"\baugmentation\b",
    r"\bclassification\b",
    r"\bdimensionality reduction\b",
    r"\bmutual information\b",
    r"\bfeature selection\b",
    r"\barchitecture\b",
    r"\bexperimental protocol\b",
    r"\bvalidation\b",
]


def _guess_method_name(article: Article, sections: Dict[str, str]) -> str:
    title = _safe_text(article.title, 220)

    # Cas fréquents : "AdvMask: ..."
    m = re.match(r"^\s*([A-Za-z][A-Za-z0-9_\-]{2,40})\s*[:—-]\s+", title)
    if m:
        candidate = m.group(1).strip()
        if candidate.lower() not in {"a", "an", "the"}:
            return candidate

    # Acronymes ou noms de méthode dans le titre.
    candidates = re.findall(r"\b[A-Z][A-Za-z0-9]{2,20}\b", title)
    stop = {
        "IEEE", "AI", "DL", "ML", "Based", "Data", "Deep", "Learning",
        "Classification",
    }

    for c in candidates:
        if c not in stop and not c.isupper():
            return c

    # Chercher dans la section méthode.
    method_text = sections.get("method", "") or sections.get("abstract", "")
    method_patterns = [
        r"(?i)\bcalled\s+([A-Z][A-Za-z0-9_\-]{2,40})\b",
        r"(?i)\bnamed\s+([A-Z][A-Za-z0-9_\-]{2,40})\b",
        r"(?i)\bwe\s+propose\s+([A-Z][A-Za-z0-9_\-]{2,40})\b",
        r"(?i)\bwe\s+introduce\s+([A-Z][A-Za-z0-9_\-]{2,40})\b",
    ]

    for pat in method_patterns:
        m = re.search(pat, method_text)
        if m:
            return m.group(1).strip()

    return "Non explicitement nommé"


def _technical_evidence_to_text(sections: Dict[str, str], limitation_evidence: Dict[str, Any], max_chars: int = 14000) -> str:
    parts = [
        "ABSTRACT:",
        _safe_text(sections.get("abstract"), 2500),
        "\nMETHODE:",
        _safe_text(sections.get("method"), 4500),
        "\nEXPERIMENTS / EVALUATION:",
        _safe_text(sections.get("experiments"), 2500),
        "\nRESULTATS:",
        _safe_text(sections.get("results"), 2500),
        "\nLIMITES / CONTRAINTES / GAP:",
        _limitation_evidence_to_text(limitation_evidence, max_chars=5000),
    ]

    return _safe_text("\n".join(parts), max_chars)



# ============================================================
# V3.0 — Deep Technical Reading / Narrative Capsule
# ============================================================

def _is_placeholder_text(value: Any) -> bool:
    text = _safe_text(value).lower()
    if not text:
        return True
    placeholders = [
        "non explicitement indiqué",
        "aucune information",
        "aucun gap",
        "les auteurs ne formulent pas",
        "les auteurs ne mentionnent pas",
        "not explicitly stated",
        "not specified",
    ]
    return any(p in text for p in placeholders)


def _clean_list_of_text(values: Any, max_items: int = 6, max_chars: int = 650) -> List[str]:
    if values is None:
        return []

    raw_values: List[Any]
    if isinstance(values, list):
        raw_values = values
    else:
        raw_values = [values]

    out: List[str] = []
    seen = set()

    for value in raw_values:
        if isinstance(value, dict):
            text_value = (
                value.get("texte")
                or value.get("text")
                or value.get("step")
                or value.get("quote")
                or value.get("sentence")
                or value.get("description")
                or ""
            )
        else:
            text_value = value

        txt = clean_article_text(_safe_text(text_value, max_chars))
        if not txt or _is_placeholder_text(txt):
            continue

        # Évite les traces de papier brut dans les champs rédactionnels.
        txt = re.sub(r"\bIn this paper,\s*", "", txt, flags=re.I)
        txt = re.sub(r"\bwe propose\b", "les auteurs proposent", txt, flags=re.I)
        txt = re.sub(r"\bwe present\b", "les auteurs présentent", txt, flags=re.I)
        txt = re.sub(r"\bwe introduce\b", "les auteurs introduisent", txt, flags=re.I)

        key = _norm_key(txt)[:260]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(txt)

        if len(out) >= max_items:
            break

    return out


def _action_sentences_for_deep_reading(
    sections: Dict[str, str],
    full_text: str = "",
    max_items: int = 12,
) -> List[str]:
    """
    Sélectionne les phrases qui décrivent réellement le mécanisme :
    proposer, utiliser, transformer, générer, entraîner, évaluer, comparer, valider.
    Générique : aucun nom de domaine n'est codé en dur.
    """
    blocks = [
        sections.get("abstract", ""),
        sections.get("method", ""),
        sections.get("experiments", ""),
        sections.get("results", ""),
        sections.get("conclusion", ""),
    ]

    action_patterns = [
        r"\bpropos(e|es|ed|ing|ons|ent)\b",
        r"\bpresent(s|ed|ing)?\b",
        r"\bintroduc(e|es|ed|ing)\b",
        r"\buse(s|d|ing)?\b",
        r"\butilis(e|er|ent|ons|é|ée|és|ées)\b",
        r"\bemploy(s|ed|ing)?\b",
        r"\bconsist(s|ed|ing)?\b",
        r"\bcomport(e|ent|er)\b",
        r"\btransform(s|ed|ing)?\b",
        r"\btransport\b",
        r"\bgenerat(e|es|ed|ing)\b",
        r"\bgén(è|e)r(e|er|é|ée|és|ées)\b",
        r"\bsynthesi[sz](e|es|ed|ing)\b",
        r"\bsynth(è|e)tis(e|er|é|ée|és|ées)\b",
        r"\btrain(s|ed|ing)?\b",
        r"\bentra[iî]n(e|er|é|ée|és|ées)\b",
        r"\bevaluat(e|es|ed|ing)\b",
        r"\bévalu(e|er|é|ée|és|ées)\b",
        r"\bcompar(e|es|ed|ing)\b",
        r"\bcompar(e|er|é|ée|és|ées|aison)\b",
        r"\bvalidat(e|es|ed|ing)\b",
        r"\bvalid(e|er|é|ée|és|ées|ation)\b",
        r"\bmeasure(s|d|ment)?\b",
        r"\bmesur(e|er|é|ée|és|ées)\b",
        r"\binput\b|\boutput\b|\bentrée\b|\bsortie\b",
        r"\bdistribution(s)?\b",
        r"\bclassif(y|ies|ied|ier)\b|\bclassifi(e|er|é|ée|és|ées)\b",
    ]

    candidates: List[Tuple[int, str]] = []

    for block_idx, block in enumerate(blocks):
        if not block:
            continue

        for sent in _sentence_split_for_evidence(block):
            if not sent:
                continue

            low = sent.lower()
            score = 0

            for pat in action_patterns:
                if re.search(pat, low, flags=re.I):
                    score += 2

            # Bonus pour phrases structurantes méthode/validation.
            if block_idx in {1, 2}:
                score += 2
            if re.search(r"\b(first|then|finally|ensuite|puis|enfin|therefore|ainsi)\b", low, flags=re.I):
                score += 2
            if len(sent) > 900:
                score -= 2

            if score > 0:
                candidates.append((score, clean_article_text(_safe_text(sent, 700))))

    candidates.sort(key=lambda x: (-x[0], len(x[1])))

    out: List[str] = []
    seen = set()
    for _, sent in candidates:
        key = _norm_key(sent)[:260]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(sent)
        if len(out) >= max_items:
            break

    return out


def _build_mechanism_chain_from_text(
    sections: Dict[str, str],
    card: Dict[str, Any],
    full_text: str = "",
) -> List[str]:
    """
    Construit une chaîne mécanisme déterministe depuis les meilleures phrases.
    Le résultat n'est pas le texte final ; il sert de matière structurée.
    """
    action_sents = _action_sentences_for_deep_reading(
        sections=sections,
        full_text=full_text,
        max_items=max(TECHNICAL_CHAIN_MAX_STEPS, 6),
    )

    if not action_sents:
        seed = card.get("methode") or card.get("technical_principle") or sections.get("method") or sections.get("abstract")
        action_sents = _sentences(seed)[:TECHNICAL_CHAIN_MAX_STEPS]

    chain = _clean_list_of_text(action_sents, max_items=TECHNICAL_CHAIN_MAX_STEPS, max_chars=520)

    if not chain:
        return ["Le mécanisme technique n'est pas suffisamment détaillé dans le texte extrait."]

    return chain


def _join_concise(values: List[str], max_chars: int = 900) -> str:
    clean = _clean_list_of_text(values, max_items=6, max_chars=500)
    if not clean:
        return ""
    return _safe_text(" ".join(clean), max_chars)


def _normalize_technical_narrative_capsule(
    raw: Any,
    fallback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    fallback = fallback or {}
    raw_dict = raw if isinstance(raw, dict) else {}

    def pick_text(key: str, alt_keys: Optional[List[str]] = None, max_chars: int = 1000) -> str:
        keys = [key] + (alt_keys or [])
        for k in keys:
            v = raw_dict.get(k)
            txt = clean_article_text(_safe_text(v, max_chars))
            if txt and not _is_placeholder_text(txt):
                return txt
        v = fallback.get(key)
        return clean_article_text(_safe_text(v, max_chars))

    chain = _clean_list_of_text(
        raw_dict.get("technical_mechanism_chain")
        or raw_dict.get("mechanism_chain")
        or raw_dict.get("technical_chain")
        or fallback.get("technical_mechanism_chain")
        or fallback.get("mechanism_chain")
        or [],
        max_items=TECHNICAL_CHAIN_MAX_STEPS,
        max_chars=650,
    )

    evidence_quotes = raw_dict.get("evidence_quotes")
    if not isinstance(evidence_quotes, list):
        evidence_quotes = fallback.get("evidence_quotes") or []

    capsule = {
        "capsule_version": "v3_1_deep_technical_reading_adaptive",
        "problem_to_solve": pick_text("problem_to_solve", ["scientific_problem", "probleme_scientifique"], 1000),
        "technical_principle_explained": pick_text("technical_principle_explained", ["technical_principle", "principle"], 1200),
        "technical_mechanism_chain": chain,
        "data_flow": pick_text("data_flow", ["data_pipeline", "inputs_outputs"], 1200),
        "model_or_algorithm_flow": pick_text("model_or_algorithm_flow", ["model_flow", "algorithm_flow", "mechanism"], 1200),
        "validation_flow": pick_text("validation_flow", ["validation_protocol", "evaluation"], 1200),
        "demonstrated_result": pick_text("demonstrated_result", ["reported_results", "resultat"], 1000),
        "non_demonstrated_or_limits": pick_text("non_demonstrated_or_limits", ["limitations", "concept_limits", "transposability_limits"], 1200),
        "transposition_to_project": pick_text("transposition_to_project", ["impact_on_verrou", "cir_exploitation"], 1300),
        "consultant_explanation_seed": pick_text("consultant_explanation_seed", ["narrative_seed", "explanation_seed"], TECHNICAL_CAPSULE_MAX_CHARS),
        "evidence_quotes": evidence_quotes[:8] if isinstance(evidence_quotes, list) else [],
        "source": _safe_text(raw_dict.get("source") or fallback.get("source") or "template", 80),
    }

    if not capsule["consultant_explanation_seed"]:
        pieces = [
            capsule["problem_to_solve"],
            capsule["technical_principle_explained"],
            _join_concise(capsule["technical_mechanism_chain"], 1000),
            capsule["validation_flow"],
            capsule["non_demonstrated_or_limits"],
            capsule["transposition_to_project"],
        ]
        capsule["consultant_explanation_seed"] = _safe_text(" ".join([p for p in pieces if p]), TECHNICAL_CAPSULE_MAX_CHARS)

    if not capsule["technical_mechanism_chain"]:
        capsule["technical_mechanism_chain"] = ["Le mécanisme technique n'est pas suffisamment détaillé dans le texte extrait."]

    return capsule


def _is_weak_capsule_text(value: Any) -> bool:
    txt = _safe_text(value, 400).strip()
    if not txt:
        return True
    if _is_placeholder_text(txt):
        return True
    low = txt.lower()
    weak_markers = [
        "non explicitement indiqué",
        "pas suffisamment détaillé",
        "aucune information",
        "n'est pas suffisamment",
        "les auteurs ne mentionnent pas",
    ]
    return len(txt) < 70 or any(m in low for m in weak_markers)


def _score_technical_narrative_capsule(capsule: Any) -> int:
    """Score générique 0..10 pour décider si le mode adaptive doit approfondir."""
    cap = _as_dict(capsule)
    if not cap:
        return 0

    score = 0

    text_fields = [
        "problem_to_solve",
        "technical_principle_explained",
        "data_flow",
        "model_or_algorithm_flow",
        "validation_flow",
        "demonstrated_result",
        "non_demonstrated_or_limits",
        "transposition_to_project",
        "consultant_explanation_seed",
    ]

    for key in text_fields:
        if not _is_weak_capsule_text(cap.get(key)):
            score += 1

    chain = cap.get("technical_mechanism_chain")
    if isinstance(chain, list):
        strong_steps = [x for x in chain if not _is_weak_capsule_text(x)]
        if len(strong_steps) >= 2:
            score += 1
        if len(strong_steps) >= 4:
            score += 1

    # Max volontairement plafonné à 10 pour lisibilité.
    return min(10, score)


def _should_use_extra_llm_for_article(card: Dict[str, Any], article: Article) -> bool:
    """
    Décision permanente multi-projets.
    - full/rich/all : ancien mode complet, toujours extra LLM.
    - core : jamais extra LLM.
    - adaptive : extra LLM seulement si la capsule est faible.
    """
    strategy = (LLM_STRATEGY or "adaptive").lower()

    if strategy in {"full", "rich", "all"}:
        return True

    if strategy in {"core", "fast", "llm_core"}:
        return False

    if strategy != "adaptive":
        return False

    if not ADAPTIVE_EXTRA_LLM_ON_WEAK_CARD:
        return False

    score = _score_technical_narrative_capsule(card.get("technical_narrative_capsule"))

    # Si la capsule principale est déjà riche, on évite les 2 appels LLM supplémentaires.
    return score < ADAPTIVE_CAPSULE_MIN_SCORE


def _template_technical_narrative_capsule(
    article: Article,
    sections: Dict[str, str],
    card: Dict[str, Any],
    full_text: str = "",
) -> Dict[str, Any]:
    chain = _build_mechanism_chain_from_text(sections, card, full_text=full_text)

    limitation_items = []
    for value in card.get("concept_limits") or []:
        limitation_items.append(value)
    for value in card.get("transposability_limits") or []:
        limitation_items.append(value)
    if card.get("limite_pour_notre_projet"):
        limitation_items.append(card.get("limite_pour_notre_projet"))

    problem = (
        card.get("probleme_scientifique")
        or sections.get("abstract")
        or sections.get("introduction")
        or article.title
    )
    principle = (
        card.get("technical_principle")
        or card.get("methode")
        or sections.get("method")
        or card.get("abstract")
    )

    fallback = {
        "problem_to_solve": _limit_sentences(problem, 3, 900),
        "technical_principle_explained": _limit_sentences(principle, 4, 1100),
        "technical_mechanism_chain": chain,
        "data_flow": _limit_sentences(card.get("jeu_de_donnees") or sections.get("experiments") or sections.get("method"), 3, 900),
        "model_or_algorithm_flow": _limit_sentences(card.get("mechanism") or card.get("methode") or sections.get("method"), 4, 1000),
        "validation_flow": _limit_sentences(card.get("evaluation") or sections.get("experiments"), 3, 900),
        "demonstrated_result": _limit_sentences(card.get("resultat") or sections.get("results"), 3, 900),
        "non_demonstrated_or_limits": _join_concise(_clean_list_of_text(limitation_items, 5, 550), 1100),
        "transposition_to_project": _limit_sentences(card.get("impact_on_verrou") or card.get("cir_exploitation") or card.get("limite_pour_notre_projet"), 3, 1100),
        "source": "template_deep_reading",
    }

    return _normalize_technical_narrative_capsule({}, fallback=fallback)


def _infer_technical_family_generic(article: Article, sections: Dict[str, str], card: Dict[str, Any]) -> str:
    """
    Inférence générique, sans noms de domaine/projet codés en dur.
    Elle classe seulement par fonction scientifique.
    """
    text_blob = _norm_key(" ".join([
        str(getattr(article, "title", "") or ""),
        card.get("methode") or "",
        card.get("technical_principle") or "",
        sections.get("method") or "",
        sections.get("abstract") or "",
    ]))

    families = [
        (
            "génération ou augmentation de données",
            [r"\baugmentation\b", r"\baugment\b", r"\bgenerat", r"\bsynth", r"\bsynthetic\b", r"\btransform"],
        ),
        (
            "apprentissage supervisé et classification",
            [r"\bclassif", r"\bsupervised\b", r"\bclassifier\b", r"\bclassification\b", r"\bneural network\b", r"\bdeep learning\b"],
        ),
        (
            "modélisation ou représentation du signal/des données",
            [r"\bmodel", r"\brepresentation\b", r"\bsignal\b", r"\bsparse\b", r"\bfeature\b", r"\bembedding\b"],
        ),
        (
            "adaptation, transfert ou réduction d'écart entre distributions",
            [r"\bdomain\b", r"\badaptation\b", r"\btransfer\b", r"\bdistribution\b", r"\bshift\b", r"\btransport\b"],
        ),
        (
            "validation, robustesse et généralisation",
            [r"\bvalidation\b", r"\brobust", r"\bgeneralization\b", r"\buncertainty\b", r"\bevaluation\b", r"\bbenchmark\b"],
        ),
        (
            "optimisation, sélection ou réglage de paramètres",
            [r"\boptimization\b", r"\bparameter\b", r"\bhyperparameter\b", r"\bselection\b", r"\bregularization\b"],
        ),
    ]

    best_label = ""
    best_score = 0
    for label, patterns in families:
        score = sum(1 for pat in patterns if re.search(pat, text_blob, flags=re.I))
        if score > best_score:
            best_label = label
            best_score = score

    return best_label or "concept ou méthode scientifique extrait du texte"


def _template_technical_method_analysis(
    article: Article,
    sections: Dict[str, str],
    card: Dict[str, Any],
) -> Dict[str, Any]:
    method_name = _best_method_candidate_from_scientific_entities(card) or _guess_method_name(article, sections)

    method = _limit_sentences(card.get("methode") or sections.get("method") or sections.get("abstract"), 5, 1400)
    contribution = _limit_sentences(card.get("apport_scientifique") or card.get("contribution") or sections.get("abstract"), 4, 1100)
    results = _limit_sentences(card.get("resultat") or sections.get("results") or sections.get("experiments"), 4, 1100)
    limits = _limit_sentences(card.get("limites") or card.get("limite_pour_notre_projet"), 4, 1100)
    gap = _limit_sentences(card.get("limite_pour_notre_projet") or card.get("gap_pour_notre_projet_resume"), 4, 1100)

    technical_family = _infer_technical_family_generic(article, sections, card)

    capsule = _template_technical_narrative_capsule(
        article=article,
        sections=sections,
        card={**card, "technical_principle": method},
        full_text="",
    )

    return {
        "method_name": method_name,
        "technical_family": technical_family,
        "technical_principle": method or "Non explicitement indiqué dans le texte extrait.",
        "mechanism": _join_concise(capsule.get("technical_mechanism_chain") or [], 1300) or method or "Non explicitement indiqué dans le texte extrait.",
        "inputs_outputs": capsule.get("data_flow") or "Non explicitement indiqué dans le texte extrait.",
        "hypotheses_or_prerequisites": card.get("contraintes_methodologiques_resume") or "Non explicitement indiqué dans le texte extrait.",
        "validation_protocol": _limit_sentences(card.get("evaluation") or sections.get("experiments"), 4, 1100) or "Non explicitement indiqué dans le texte extrait.",
        "reported_results": results or "Non explicitement indiqué dans le texte extrait.",
        "concept_contribution": contribution or "Non explicitement indiqué dans le texte extrait.",
        "concept_limits": [
            limits or "Les limites du concept ne sont pas explicitement détaillées dans le texte extrait."
        ],
        "transposability_limits": [
            gap or "La transposition au contexte du projet nécessite une validation spécifique."
        ],
        "impact_on_verrou": (
            "L'article fournit un appui scientifique, mais la capacité du mécanisme à lever directement le verrou "
            "reste dépendante des données, du protocole et des conditions d'utilisation du projet."
        ),
        "remaining_uncertainty": (
            "La représentativité, la robustesse, les paramètres de validation et la généralisation doivent être vérifiés "
            "dans le contexte propre du dossier."
        ),
        "cir_exploitation": (
            "Ce concept peut être mobilisé dans l'état de l'art comme mécanisme scientifique explicatif, "
            "mais il ne doit pas être présenté comme une solution directement transposable sans validation expérimentale."
        ),
        "technical_mechanism_chain": capsule.get("technical_mechanism_chain") or [],
        "data_flow": capsule.get("data_flow"),
        "model_or_algorithm_flow": capsule.get("model_or_algorithm_flow"),
        "validation_flow": capsule.get("validation_flow"),
        "demonstrated_result": capsule.get("demonstrated_result"),
        "non_demonstrated_or_limits": capsule.get("non_demonstrated_or_limits"),
        "transposition_to_project": capsule.get("transposition_to_project"),
        "consultant_explanation_seed": capsule.get("consultant_explanation_seed"),
        "technical_narrative_capsule": capsule,
        "evidence_quotes": [],
        "source": "template_deep_reading",
    }


def summarize_technical_method_with_llm(
    article: Article,
    authors: List[str],
    sections: Dict[str, str],
    card: Dict[str, Any],
    limitation_evidence: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    method_name_hint = _guess_method_name(article, sections)
    technical_context = _technical_evidence_to_text(sections, limitation_evidence)

    author_label = _author_label(authors)
    year = article.year or "s.d."

    prompt = f"""
Tu es un expert scientifique chargé de préparer une fiche article pour un état de l'art CIR.

Ta mission :
Extraire une compréhension technique profonde du concept ou de la méthode décrite dans l'article.

Ce qu'on cherche :
- pas un résumé article par article ;
- une chaîne technique explicative, comme un consultant qui explique le mécanisme ;
- le passage logique : problème scientifique → principe technique → données → modèle/algorithme → validation → résultat → limite de transposition.

Règles strictes :
- Réponds uniquement en JSON valide.
- N'invente jamais.
- Utilise uniquement le texte fourni.
- Si une information est absente, écris exactement : "Non explicitement indiqué dans le texte extrait."
- Ne transforme pas une hypothèse en résultat.
- Ne transforme pas une perspective en limite prouvée.
- Ne cite pas Memory V2.
- Garde les noms propres, acronymes, datasets, métriques et noms de méthodes tels qu'ils apparaissent dans l'article.
- N'écris pas "cet article résume" ; explique le mécanisme.
- La chaîne technique doit contenir des étapes concrètes, dans l'ordre logique, pas des mots-clés.

JSON attendu exactement :
{{
  "technical_method_analysis": {{
    "method_name": "...",
    "technical_family": "...",
    "technical_principle": "...",
    "mechanism": "...",
    "technical_mechanism_chain": ["étape 1", "étape 2", "étape 3"],
    "inputs_outputs": "...",
    "data_flow": "...",
    "model_or_algorithm_flow": "...",
    "hypotheses_or_prerequisites": "...",
    "validation_protocol": "...",
    "validation_flow": "...",
    "reported_results": "...",
    "demonstrated_result": "...",
    "concept_contribution": "...",
    "concept_limits": ["...", "..."],
    "transposability_limits": ["...", "..."],
    "non_demonstrated_or_limits": "...",
    "impact_on_verrou": "...",
    "remaining_uncertainty": "...",
    "cir_exploitation": "...",
    "transposition_to_project": "...",
    "consultant_explanation_seed": "...",
    "evidence_quotes": [
      {{"field": "technical_principle", "quote": "..."}},
      {{"field": "technical_mechanism_chain", "quote": "..."}}
    ]
  }}
}}

Aide de contexte :
- Article : {article.title}
- Auteur/année : {author_label} ({year})
- Nom de méthode détecté automatiquement : {method_name_hint}

Texte à utiliser :
{technical_context}
""".strip()

    result = _call_ollama_json(prompt, task=f"technical_article_{article.id}")
    if not result:
        return None

    analysis = result.get("technical_method_analysis")
    if not isinstance(analysis, dict):
        return None

    def clean_list(values: Any, max_items: int = 6) -> List[str]:
        return _clean_list_of_text(values, max_items=max_items, max_chars=750)

    cleaned = {
        "method_name": clean_article_text(_safe_text(analysis.get("method_name"), 180)) or method_name_hint,
        "technical_family": clean_article_text(_safe_text(analysis.get("technical_family"), 300)),
        "technical_principle": clean_article_text(_safe_text(analysis.get("technical_principle"), 1400)),
        "mechanism": clean_article_text(_safe_text(analysis.get("mechanism"), 1600)),
        "technical_mechanism_chain": clean_list(analysis.get("technical_mechanism_chain"), TECHNICAL_CHAIN_MAX_STEPS),
        "inputs_outputs": clean_article_text(_safe_text(analysis.get("inputs_outputs"), 1000)),
        "data_flow": clean_article_text(_safe_text(analysis.get("data_flow"), 1200)),
        "model_or_algorithm_flow": clean_article_text(_safe_text(analysis.get("model_or_algorithm_flow"), 1200)),
        "hypotheses_or_prerequisites": clean_article_text(_safe_text(analysis.get("hypotheses_or_prerequisites"), 1100)),
        "validation_protocol": clean_article_text(_safe_text(analysis.get("validation_protocol"), 1200)),
        "validation_flow": clean_article_text(_safe_text(analysis.get("validation_flow"), 1200)),
        "reported_results": clean_article_text(_safe_text(analysis.get("reported_results"), 1100)),
        "demonstrated_result": clean_article_text(_safe_text(analysis.get("demonstrated_result"), 1100)),
        "concept_contribution": clean_article_text(_safe_text(analysis.get("concept_contribution"), 1100)),
        "concept_limits": clean_list(analysis.get("concept_limits"), 6),
        "transposability_limits": clean_list(analysis.get("transposability_limits"), 6),
        "non_demonstrated_or_limits": clean_article_text(_safe_text(analysis.get("non_demonstrated_or_limits"), 1200)),
        "impact_on_verrou": clean_article_text(_safe_text(analysis.get("impact_on_verrou"), 1300)),
        "remaining_uncertainty": clean_article_text(_safe_text(analysis.get("remaining_uncertainty"), 1300)),
        "cir_exploitation": clean_article_text(_safe_text(analysis.get("cir_exploitation"), 1300)),
        "transposition_to_project": clean_article_text(_safe_text(analysis.get("transposition_to_project"), 1300)),
        "consultant_explanation_seed": clean_article_text(_safe_text(analysis.get("consultant_explanation_seed"), TECHNICAL_CAPSULE_MAX_CHARS)),
        "evidence_quotes": analysis.get("evidence_quotes") if isinstance(analysis.get("evidence_quotes"), list) else [],
        "source": "llm_deep_reading",
    }

    fallback = _template_technical_method_analysis(article, sections, card)

    for key, value in list(cleaned.items()):
        if key in {"concept_limits", "transposability_limits", "technical_mechanism_chain"}:
            if not value:
                cleaned[key] = fallback.get(key, [])
        elif key != "evidence_quotes" and not value:
            cleaned[key] = fallback.get(key, "Non explicitement indiqué dans le texte extrait.")

    capsule = _normalize_technical_narrative_capsule(
        {
            "problem_to_solve": card.get("probleme_scientifique"),
            "technical_principle_explained": cleaned.get("technical_principle"),
            "technical_mechanism_chain": cleaned.get("technical_mechanism_chain"),
            "data_flow": cleaned.get("data_flow") or cleaned.get("inputs_outputs"),
            "model_or_algorithm_flow": cleaned.get("model_or_algorithm_flow") or cleaned.get("mechanism"),
            "validation_flow": cleaned.get("validation_flow") or cleaned.get("validation_protocol"),
            "demonstrated_result": cleaned.get("demonstrated_result") or cleaned.get("reported_results"),
            "non_demonstrated_or_limits": cleaned.get("non_demonstrated_or_limits") or " ".join(cleaned.get("concept_limits") or []),
            "transposition_to_project": cleaned.get("transposition_to_project") or cleaned.get("impact_on_verrou") or cleaned.get("cir_exploitation"),
            "consultant_explanation_seed": cleaned.get("consultant_explanation_seed"),
            "evidence_quotes": cleaned.get("evidence_quotes"),
            "source": "llm_deep_reading",
        },
        fallback=fallback.get("technical_narrative_capsule") or {},
    )

    cleaned["technical_narrative_capsule"] = capsule
    return cleaned


def apply_technical_method_layer(
    card: Dict[str, Any],
    article: Article,
    authors: List[str],
    sections: Dict[str, str],
    full_text: str,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """
    V3.0 :
    produit une compréhension technique profonde exploitable par Phase 4.5, 4.7 et 5.

    Elle relie :
    problème -> principe -> chaîne de mécanisme -> données -> modèle -> validation -> limite -> verrou.
    """

    limitation_evidence = card.get("evidence", {}).get("limitation_evidence")
    if not isinstance(limitation_evidence, dict):
        limitation_evidence = extract_limitation_evidence(sections, full_text)

    prebuilt_capsule = card.get("technical_narrative_capsule")
    analysis = summarize_technical_method_with_llm(
        article=article,
        authors=authors,
        sections=sections,
        card=card,
        limitation_evidence=limitation_evidence,
    ) if use_llm else None

    if not analysis:
        analysis = _template_technical_method_analysis(article, sections, card)

    if prebuilt_capsule and isinstance(prebuilt_capsule, dict):
        # La fiche principale LLM peut déjà contenir une capsule.
        # On la garde si elle est plus narrative, tout en utilisant l'analyse technique comme fallback.
        capsule = _normalize_technical_narrative_capsule(
            prebuilt_capsule,
            fallback=analysis.get("technical_narrative_capsule") or {},
        )
        analysis["technical_narrative_capsule"] = capsule
    else:
        capsule = _normalize_technical_narrative_capsule(
            analysis.get("technical_narrative_capsule") or {},
            fallback=_template_technical_narrative_capsule(article, sections, {**card, **analysis}, full_text=full_text),
        )
        analysis["technical_narrative_capsule"] = capsule

    # Champs plats pour compatibilité avec Phase 4.5 / writer.
    card["technical_method_analysis"] = analysis
    card["method_name"] = analysis.get("method_name")
    card["technical_family"] = analysis.get("technical_family")
    card["technical_principle"] = analysis.get("technical_principle")
    card["mechanism"] = analysis.get("mechanism")
    card["technical_mechanism_chain"] = analysis.get("technical_mechanism_chain") or capsule.get("technical_mechanism_chain") or []
    card["data_flow"] = analysis.get("data_flow") or capsule.get("data_flow")
    card["model_or_algorithm_flow"] = analysis.get("model_or_algorithm_flow") or capsule.get("model_or_algorithm_flow")
    card["validation_flow"] = analysis.get("validation_flow") or capsule.get("validation_flow")
    card["demonstrated_result"] = analysis.get("demonstrated_result") or capsule.get("demonstrated_result")
    card["non_demonstrated_or_limits"] = analysis.get("non_demonstrated_or_limits") or capsule.get("non_demonstrated_or_limits")
    card["transposition_to_project"] = analysis.get("transposition_to_project") or capsule.get("transposition_to_project")
    card["consultant_explanation_seed"] = analysis.get("consultant_explanation_seed") or capsule.get("consultant_explanation_seed")
    card["technical_narrative_capsule"] = capsule

    card["concept_limits"] = analysis.get("concept_limits") or []
    card["transposability_limits"] = analysis.get("transposability_limits") or []
    card["impact_on_verrou"] = analysis.get("impact_on_verrou")
    card["remaining_uncertainty"] = analysis.get("remaining_uncertainty")
    card["cir_exploitation"] = analysis.get("cir_exploitation")

    # Bloc prêt pour Phase 4.5.
    card["technical_concept_limits"] = {
        "method_name": card.get("method_name"),
        "technical_family": card.get("technical_family"),
        "principle": card.get("technical_principle"),
        "mechanism": card.get("mechanism"),
        "technical_mechanism_chain": card.get("technical_mechanism_chain"),
        "data_flow": card.get("data_flow"),
        "model_or_algorithm_flow": card.get("model_or_algorithm_flow"),
        "validation_flow": card.get("validation_flow"),
        "demonstrated_result": card.get("demonstrated_result"),
        "non_demonstrated_or_limits": card.get("non_demonstrated_or_limits"),
        "transposition_to_project": card.get("transposition_to_project"),
        "consultant_explanation_seed": card.get("consultant_explanation_seed"),
        "concept_limits": card.get("concept_limits"),
        "transposability_limits": card.get("transposability_limits"),
        "impact_on_verrou": card.get("impact_on_verrou"),
        "remaining_uncertainty": card.get("remaining_uncertainty"),
        "cir_exploitation": card.get("cir_exploitation"),
        "source": analysis.get("source"),
    }

    card.setdefault("evidence", {})["technical_method_analysis_source"] = analysis.get("source")
    card.setdefault("evidence", {})["technical_method_analysis_available"] = True
    card.setdefault("evidence", {})["deep_technical_reading_enabled"] = bool(DEEP_TECHNICAL_READING_ENABLED)
    card.setdefault("evidence", {})["technical_narrative_capsule_available"] = bool(capsule)

    return card




# ============================================================
# V3.2 — Extractive scientific paragraph layer (no LLM)
# ============================================================

EXTRACTIVE_BUCKET_LABELS = {
    "problem": "problème scientifique / limite initiale",
    "solution": "solution ou contribution proposée",
    "method": "méthode / principe technique",
    "workflow": "enchaînement technique / étapes",
    "definition": "définition ou clarification de concept",
    "dataset": "données, datasets ou conditions d'observation",
    "validation": "protocole expérimental / validation",
    "results": "résultats démontrés",
    "limitations": "limites, contraintes ou non-transposition",
    "future_work": "travaux futurs / ouvertures",
}


def _paragraphs_from_text(text: str, max_chars: int = EXTRACTIVE_MAX_PARAGRAPH_CHARS) -> List[str]:
    """
    Découpe conservatrice : on garde des paragraphes proches du texte original.
    On évite de transformer l'article en résumé.
    """
    text = clean_article_text(text or "")
    if not text:
        return []

    # Les extractions PDF ont parfois de simples retours ligne. On segmente d'abord sur doubles retours,
    # puis on découpe les très gros blocs par phrases.
    raw_blocks = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    out: List[str] = []

    for block in raw_blocks:
        block = clean_article_text(block)
        if not block:
            continue
        if len(block) <= max_chars:
            out.append(block)
            continue

        sents = _sentence_split_for_evidence(block)
        buffer = ""
        for sent in sents:
            if not buffer:
                buffer = sent
            elif len(buffer) + len(sent) + 1 <= max_chars:
                buffer += " " + sent
            else:
                if buffer:
                    out.append(buffer.strip())
                buffer = sent
        if buffer:
            out.append(buffer.strip())

    clean_out: List[str] = []
    seen = set()
    for p in out:
        p = clean_article_text(_safe_text(p, max_chars))
        if not (80 <= len(p) <= max_chars):
            continue
        if _matches_any_pattern(p, GENERIC_NOISE_PATTERNS):
            continue
        key = _norm_key(p)[:260]
        if not key or key in seen:
            continue
        seen.add(key)
        clean_out.append(p)
    return clean_out


def _extractive_section_paragraphs(sections: Dict[str, str], full_text: str = "") -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    section_order = [
        "abstract", "introduction", "related_work", "method", "experiments",
        "results", "limitations", "future_work", "conclusion",
    ]

    for section in section_order:
        for idx, para in enumerate(_paragraphs_from_text(sections.get(section, ""))):
            items.append({
                "section": section,
                "paragraph_index": idx,
                "text": para,
                "source": "detected_section",
            })

    # Sécurité : certains parseurs de sections ratent un passage méthode/dataset/résultat.
    # On ajoute un scan limité du full text en high-value uniquement.
    preview = _safe_text(full_text, EXTRACTIVE_FULLTEXT_SCAN_CHARS)
    if preview:
        for idx, para in enumerate(_paragraphs_from_text(preview)):
            if not _extractive_has_any_signal(para):
                continue
            items.append({
                "section": "full_text_high_value",
                "paragraph_index": idx,
                "text": para,
                "source": "full_text_scan",
            })

    # Dédup global.
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in items:
        key = _norm_key(item.get("text"))[:280]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _extractive_signal_patterns() -> Dict[str, List[str]]:
    # Patterns génériques multi-domaines : aucune liste propre à un projet.
    return {
        "problem": [
            r"\bproblem\b", r"\bchallenge\b", r"\bdifficult\b", r"\bdifficulty\b",
            r"\blimited\b", r"\bscarce\b", r"\black of\b", r"\binsufficient\b",
            r"\bnot enough\b", r"\bpoor generalization\b", r"\bsuboptimal\b",
            r"\bverrou\b", r"\bprobl[èe]me\b", r"\bdifficult[ée]\b", r"\bmanque\b",
        ],
        "solution": [
            r"\bwe propose\b", r"\bwe present\b", r"\bwe introduce\b", r"\bwe develop\b",
            r"\bproposed\b", r"\bnovel\b", r"\bsolution\b", r"\bapproach\b",
            r"\bcontribution\b", r"\bto solve\b", r"\bto address\b",
            r"\bnous proposons\b", r"\bnous pr[ée]sentons\b", r"\bafin de r[ée]soudre\b",
        ],
        "method": [
            r"\bmethod\b", r"\bmethodology\b", r"\bapproach\b", r"\bframework\b",
            r"\balgorithm\b", r"\bmodel\b", r"\bmodule\b", r"\barchitecture\b",
            r"\boptimization\b", r"\btraining\b", r"\blearning\b", r"\bclassifier\b",
            r"\btransformation\b", r"\bgenerate\b", r"\bsynthesize\b", r"\baugment\b",
            r"\bm[ée]thode\b", r"\bmod[èe]le\b", r"\balgorithme\b", r"\bentra[iî]nement\b",
        ],
        "workflow": [
            r"\bfirst\b", r"\bsecond\b", r"\bthen\b", r"\bfinally\b", r"\bnext\b",
            r"\bstep\b", r"\bstage\b", r"\bpipeline\b", r"\bworkflow\b", r"\bprocess\b",
            r"\binput\b", r"\boutput\b", r"\bconsists of\b", r"\bcomposed of\b",
            r"\bd'abord\b", r"\bensuite\b", r"\bpuis\b", r"\benfin\b", r"\b[ée]tape\b",
        ],
        "definition": [
            r"\bis defined as\b", r"\bis referred to as\b", r"\bdenotes\b", r"\brefers to\b",
            r"\bcalled\b", r"\bknown as\b", r"\bis a\b", r"\bare a\b", r"\bmeans\b",
            r"\best d[ée]fini\b", r"\bd[ée]signe\b", r"\bsignifie\b", r"\best appel[ée]\b",
        ],
        "dataset": [
            r"\bdataset\b", r"\bdata-set\b", r"\bbenchmark\b", r"\bcorpus\b", r"\btraining set\b",
            r"\btest set\b", r"\bvalidation set\b", r"\bdata\b", r"\bmeasurements\b",
            r"\bsamples\b", r"\bimages\b", r"\bclasses\b", r"\bobservation\b",
            r"\bjeu de donn[ée]es\b", r"\bdonn[ée]es\b", r"\b[ée]chantillons\b", r"\bmesures\b",
        ],
        "validation": [
            r"\bevaluate\b", r"\bevaluation\b", r"\bexperiment\b", r"\bexperimental\b",
            r"\bvalidation\b", r"\btest\b", r"\bmetric\b", r"\bcompare\b", r"\bbaseline\b",
            r"\bperformance\b", r"\baccuracy\b", r"\bprotocol\b", r"\bcross-validation\b",
            r"\b[ée]valuer\b", r"\bexp[ée]rience\b", r"\bprotocole\b", r"\bcomparaison\b",
        ],
        "results": [
            r"\bresults?\b", r"\bshow\b", r"\bdemonstrate\b", r"\bimprove\b", r"\boutperform\b",
            r"\bachieve\b", r"\bsignificant\b", r"\bbetter\b", r"\bgeneralization\b", r"\brobust\b",
            r"\br[ée]sultats?\b", r"\bmontrent\b", r"\bd[ée]montrent\b", r"\bam[ée]lior\b",
        ],
        "limitations": [
            r"\blimitation\b", r"\blimit\b", r"\bonly\b", r"\brestricted\b", r"\bconstraint\b",
            r"\bdepend\b", r"\brequires\b", r"\bassume\b", r"\bnot evaluated\b", r"\bnot validated\b",
            r"\bfails?\b", r"\bdrop\b", r"\bdegrade\b", r"\blocal\b", r"\bthreats? to validity\b",
            r"\blimite\b", r"\bcontrainte\b", r"\bd[ée]pend\b", r"\bnon valid[ée]\b",
        ],
        "future_work": [
            r"\bfuture work\b", r"\bfuture research\b", r"\bin future\b", r"\bnext step\b",
            r"\bwill investigate\b", r"\bwill focus\b", r"\bperspective\b", r"\btravaux futurs\b",
        ],
    }


def _extractive_has_any_signal(text: str) -> bool:
    patterns = _extractive_signal_patterns()
    return any(_matches_any_pattern(text, pats) for pats in patterns.values())


def _extractive_matched_terms(text: str, patterns: List[str], max_terms: int = 8) -> List[str]:
    out: List[str] = []
    low = text or ""
    for pat in patterns:
        if re.search(pat, low, flags=re.I):
            term = re.sub(r"\\b|\(|\)|\?|\+|\*|\[|\]|\\", "", pat)
            term = term.replace(".", "").replace("^", "").strip()
            if term and len(term) < 60 and term not in out:
                out.append(term)
        if len(out) >= max_terms:
            break
    return out


def _score_extractive_paragraph(item: Dict[str, Any], bucket: str) -> Dict[str, Any]:
    text = item.get("text") or ""
    section = item.get("section") or "unknown"
    patterns = _extractive_signal_patterns().get(bucket, [])
    score = 0

    section_bonus = {
        "problem": {"abstract": 4, "introduction": 5, "related_work": 2, "conclusion": 1},
        "solution": {"abstract": 4, "introduction": 3, "method": 4, "conclusion": 2},
        "method": {"method": 7, "abstract": 3, "introduction": 2, "full_text_high_value": 1},
        "workflow": {"method": 6, "experiments": 3, "abstract": 2, "full_text_high_value": 1},
        "definition": {"abstract": 3, "introduction": 3, "method": 3, "related_work": 3},
        "dataset": {"experiments": 7, "method": 3, "abstract": 2, "results": 2},
        "validation": {"experiments": 7, "results": 4, "method": 3, "conclusion": 1},
        "results": {"results": 7, "conclusion": 4, "abstract": 2, "experiments": 2},
        "limitations": {"limitations": 8, "future_work": 5, "conclusion": 5, "results": 2, "method": 1},
        "future_work": {"future_work": 8, "conclusion": 5, "limitations": 2},
    }.get(bucket, {})
    score += section_bonus.get(section, 0)

    matched = _extractive_matched_terms(text, patterns)
    score += len(matched) * 2

    words = _word_count(text)
    if 45 <= words <= 180:
        score += 3
    elif 20 <= words < 45 or 180 < words <= 260:
        score += 1
    elif words > 300:
        score -= 2

    # Les paragraphes qui contiennent une structure explicative sont utiles pour le LLM.
    if re.search(r"\b(because|therefore|thus|hence|so that|in order to|afin de|car|donc|ainsi)\b", text, flags=re.I):
        score += 2
    if re.search(r"\b(input|output|train|test|validate|generate|synthesize|transform|classif)\w*\b", text, flags=re.I):
        score += 2

    out = dict(item)
    out.update({
        "bucket": bucket,
        "bucket_label": EXTRACTIVE_BUCKET_LABELS.get(bucket, bucket),
        "score": score,
        "matched_terms": matched,
        "word_count": words,
        "char_count": len(text),
        "text": _safe_text(text, EXTRACTIVE_MAX_PARAGRAPH_CHARS),
    })
    return out


def _top_extractive_items(items: List[Dict[str, Any]], bucket: str, limit: int = EXTRACTIVE_MAX_PARAGRAPHS_PER_BUCKET) -> List[Dict[str, Any]]:
    scored = [_score_extractive_paragraph(x, bucket) for x in items]
    scored = [x for x in scored if x.get("score", 0) > 0]
    scored.sort(key=lambda x: (-int(x.get("score") or 0), x.get("section") or "", int(x.get("paragraph_index") or 0)))

    out: List[Dict[str, Any]] = []
    seen = set()
    for item in scored:
        key = _norm_key(item.get("text"))[:260]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _extract_definitions_from_items(items: List[Dict[str, Any]], scientific_entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    terms = []
    for ent in scientific_entities or []:
        term = _safe_text(ent.get("term"), 80)
        if term and not _is_generic_scientific_candidate(term):
            terms.append(term)
    terms = terms[:30]

    definition_items: List[Dict[str, Any]] = []
    for item in items:
        text = item.get("text") or ""
        if not _matches_any_pattern(text, _extractive_signal_patterns()["definition"]):
            continue
        linked_terms = []
        low = text.lower()
        for term in terms:
            if term.lower() in low and term not in linked_terms:
                linked_terms.append(term)
        definition_items.append({
            "section": item.get("section"),
            "paragraph_index": item.get("paragraph_index"),
            "text": _safe_text(text, EXTRACTIVE_MAX_PARAGRAPH_CHARS),
            "linked_terms": linked_terms[:8],
            "score": _score_extractive_paragraph(item, "definition").get("score", 0) + len(linked_terms),
            "source": item.get("source"),
        })

    definition_items.sort(key=lambda x: -int(x.get("score") or 0))
    return definition_items[:EXTRACTIVE_MAX_PARAGRAPHS_PER_BUCKET]


def build_extractive_article_evidence_bank(
    article: Article,
    sections: Dict[str, str],
    full_text: str,
    card: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    card = card or {}
    items = _extractive_section_paragraphs(sections, full_text=full_text)
    scientific_entities = card.get("scientific_entities") or []

    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for bucket in EXTRACTIVE_BUCKET_LABELS.keys():
        if bucket == "definition":
            continue
        buckets[bucket] = _top_extractive_items(items, bucket)

    buckets["definition"] = _extract_definitions_from_items(items, scientific_entities)

    # Compact high-value evidence for 4.5/4.7/5. Pas de résumé, seulement original paragraphs.
    reasoning_order = [
        "problem", "solution", "method", "workflow", "definition", "dataset",
        "validation", "results", "limitations", "future_work",
    ]
    reasoning_lines: List[str] = []
    for bucket in reasoning_order:
        label = EXTRACTIVE_BUCKET_LABELS.get(bucket, bucket)
        reasoning_lines.append(f"\n## {label}")
        selected = buckets.get(bucket) or []
        if not selected:
            reasoning_lines.append("- Aucun paragraphe original fortement détecté.")
            continue
        for item in selected[:4]:
            sec = item.get("section", "unknown")
            txt = _safe_text(item.get("text"), 900)
            reasoning_lines.append(f"- [{sec}] {txt}")

    return {
        "version": "v3_2_extractive_original_paragraphs_no_llm",
        "strategy": "extractive_first",
        "article_id": getattr(article, "id", None),
        "title": getattr(article, "title", None),
        "rules": {
            "phase_2_does_not_summarize": True,
            "paragraphs_are_original_extracts": True,
            "llm_should_reason_in_later_phases": True,
            "use_as_evidence_not_final_text": True,
        },
        "paragraph_buckets": buckets,
        "all_candidate_paragraphs_count": len(items),
        "bucket_counts": {k: len(v or []) for k, v in buckets.items()},
        "phase45_47_5_reasoning_context": _safe_text("\n".join(reasoning_lines), EXTRACTIVE_REASONING_CONTEXT_CHARS),
    }


def _first_bucket_text(bank: Dict[str, Any], bucket: str, max_chars: int = 1000) -> str:
    buckets = _as_dict(bank.get("paragraph_buckets"))
    items = buckets.get(bucket) or []
    if not isinstance(items, list) or not items:
        return ""
    return clean_article_text(_safe_text(items[0].get("text"), max_chars))


def _bucket_chain(bank: Dict[str, Any], max_items: int = TECHNICAL_CHAIN_MAX_STEPS) -> List[str]:
    buckets = _as_dict(bank.get("paragraph_buckets"))
    chain_items = []
    for bucket in ["workflow", "method", "dataset", "validation"]:
        for item in buckets.get(bucket) or []:
            txt = _safe_text(item.get("text"), 700)
            if txt:
                chain_items.append(txt)
    return _clean_list_of_text(chain_items, max_items=max_items, max_chars=700)


def _build_capsule_from_extractive_bank(card: Dict[str, Any], bank: Dict[str, Any]) -> Dict[str, Any]:
    fallback = _as_dict(card.get("technical_narrative_capsule"))
    problem = _first_bucket_text(bank, "problem", 1000) or card.get("probleme_scientifique") or fallback.get("problem_to_solve")
    method = _first_bucket_text(bank, "method", 1200) or card.get("methode") or fallback.get("technical_principle_explained")
    data = _first_bucket_text(bank, "dataset", 1200) or card.get("jeu_de_donnees") or fallback.get("data_flow")
    validation = _first_bucket_text(bank, "validation", 1200) or card.get("evaluation") or fallback.get("validation_flow")
    result = _first_bucket_text(bank, "results", 1000) or card.get("resultat") or fallback.get("demonstrated_result")
    limits = _first_bucket_text(bank, "limitations", 1200) or card.get("limite_pour_notre_projet") or fallback.get("non_demonstrated_or_limits")
    solution = _first_bucket_text(bank, "solution", 1000) or card.get("apport_scientifique")
    chain = _bucket_chain(bank)

    seed_parts = [
        problem,
        solution,
        method,
        data,
        validation,
        result,
        limits,
    ]
    seed = _safe_text(" ".join([p for p in seed_parts if p]), TECHNICAL_CAPSULE_MAX_CHARS)

    cap = {
        "capsule_version": "v3_2_extractive_original_paragraphs_no_llm",
        "problem_to_solve": _safe_text(problem, 1000),
        "technical_principle_explained": _safe_text(method, 1200),
        "technical_mechanism_chain": chain or fallback.get("technical_mechanism_chain") or [],
        "data_flow": _safe_text(data, 1200),
        "model_or_algorithm_flow": _safe_text(method, 1200),
        "validation_flow": _safe_text(validation, 1200),
        "demonstrated_result": _safe_text(result, 1000),
        "non_demonstrated_or_limits": _safe_text(limits, 1200),
        "transposition_to_project": _safe_text(card.get("impact_on_verrou") or card.get("cir_exploitation") or fallback.get("transposition_to_project"), 1300),
        "consultant_explanation_seed": seed,
        "evidence_quotes": [],
        "source": "extractive_original_paragraphs",
    }
    return _normalize_technical_narrative_capsule(cap, fallback=fallback)


def apply_extractive_article_reading_layer(
    card: Dict[str, Any],
    article: Article,
    sections: Dict[str, str],
    full_text: str,
) -> Dict[str, Any]:
    if not EXTRACTIVE_READING_ENABLED:
        return card

    bank = build_extractive_article_evidence_bank(article=article, sections=sections, full_text=full_text, card=card)
    card["article_evidence_bank"] = bank
    card["original_scientific_paragraphs"] = bank.get("paragraph_buckets") or {}
    card["phase2_evidence_for_reasoning"] = bank.get("phase45_47_5_reasoning_context")
    card["extractive_paragraph_counts"] = bank.get("bucket_counts") or {}

    capsule = _build_capsule_from_extractive_bank(card, bank)
    card["technical_narrative_capsule"] = capsule

    def missing_or_placeholder(value: Any) -> bool:
        normalized = _norm_key(value)
        return bool(
            not normalized
            or normalized.startswith("non explicitement indique")
            or normalized.startswith("non renseigne")
            or normalized in {"inconnu", "unknown", "n a", "na"}
        )

    extractive_backfills = {
        "probleme_scientifique": _first_bucket_text(bank, "problem", 1600),
        "methode": _first_bucket_text(bank, "method", 2200),
        "jeu_de_donnees": _first_bucket_text(bank, "dataset", 1800),
        "evaluation": _first_bucket_text(bank, "validation", 2200),
        "resultat": _first_bucket_text(bank, "results", 2200),
        "resultats": _first_bucket_text(bank, "results", 2200),
        "limite_pour_notre_projet": _first_bucket_text(
            bank, "limitations", 2200
        ),
        "limitations": _first_bucket_text(bank, "limitations", 2200),
    }
    for field, value in extractive_backfills.items():
        if value and missing_or_placeholder(card.get(field)):
            card[field] = value

    # Champs plats pour compatibilité phases 4.5/4.7/5.
    card["technical_mechanism_chain"] = capsule.get("technical_mechanism_chain") or []
    card["data_flow"] = capsule.get("data_flow")
    card["model_or_algorithm_flow"] = capsule.get("model_or_algorithm_flow")
    card["validation_flow"] = capsule.get("validation_flow")
    card["demonstrated_result"] = capsule.get("demonstrated_result")
    card["non_demonstrated_or_limits"] = capsule.get("non_demonstrated_or_limits")
    card["consultant_explanation_seed"] = capsule.get("consultant_explanation_seed")
    card["transposition_to_project"] = capsule.get("transposition_to_project")

    tech = _as_dict(card.get("technical_method_analysis"))
    tech.update({
        "technical_narrative_capsule": capsule,
        "technical_mechanism_chain": capsule.get("technical_mechanism_chain") or [],
        "data_flow": capsule.get("data_flow"),
        "model_or_algorithm_flow": capsule.get("model_or_algorithm_flow"),
        "validation_flow": capsule.get("validation_flow"),
        "demonstrated_result": capsule.get("demonstrated_result"),
        "non_demonstrated_or_limits": capsule.get("non_demonstrated_or_limits"),
        "consultant_explanation_seed": capsule.get("consultant_explanation_seed"),
        "source": "extractive_original_paragraphs",
    })
    card["technical_method_analysis"] = tech

    tc = _as_dict(card.get("technical_concept_limits"))
    tc.update({
        "technical_mechanism_chain": capsule.get("technical_mechanism_chain") or [],
        "data_flow": capsule.get("data_flow"),
        "model_or_algorithm_flow": capsule.get("model_or_algorithm_flow"),
        "validation_flow": capsule.get("validation_flow"),
        "demonstrated_result": capsule.get("demonstrated_result"),
        "non_demonstrated_or_limits": capsule.get("non_demonstrated_or_limits"),
        "consultant_explanation_seed": capsule.get("consultant_explanation_seed"),
        "source": "extractive_original_paragraphs",
    })
    card["technical_concept_limits"] = tc

    card.setdefault("evidence", {})["extractive_reading_enabled"] = True
    card.setdefault("evidence", {})["extractive_only_phase2"] = bool(EXTRACTIVE_ONLY_PHASE2)
    card.setdefault("evidence", {})["article_evidence_bank_available"] = True
    card.setdefault("evidence", {})["article_evidence_bank_version"] = bank.get("version")
    card.setdefault("evidence", {})["extractive_bucket_counts"] = bank.get("bucket_counts")
    card.setdefault("evidence", {})["technical_capsule_quality_score"] = _score_technical_narrative_capsule(capsule)
    return card

# ============================================================
# Template card
# ============================================================

def _first_non_empty(*values: str) -> str:
    for v in values:
        if v and str(v).strip():
            return str(v).strip()
    return ""


def _sync_card_source_context(
    card: Dict[str, Any],
    article: Article,
) -> Dict[str, Any]:
    """Synchronise la provenance guidée même lorsqu'une carte est réutilisée."""
    source_json = _as_dict(getattr(article, "source_json", None))
    target_verrous = list(
        source_json.get("target_verrous")
        or source_json.get("covered_verrou_ids")
        or []
    )
    card["guided_research_source"] = bool(
        source_json.get("guided_research_source")
    )
    card["guided_candidate_id"] = source_json.get("guided_candidate_id")
    card["consultant_evidence_role"] = source_json.get(
        "consultant_evidence_role"
    )
    card["candidate_kind"] = (
        source_json.get("candidate_kind")
        or (
            "scientific_article"
            if source_json.get("guided_research_source")
            else card.get("candidate_kind")
        )
    )
    card["section_ids"] = list(source_json.get("section_ids") or [])
    card["target_verrous"] = target_verrous
    card["verrou_ids"] = target_verrous
    return card


def _sync_card_visual_evidence(
    card: Dict[str, Any],
    *,
    project: Project,
    article: Article,
    citation_id: str,
    fulltext_info: Dict[str, Any],
) -> Dict[str, Any]:
    """Attache les figures originales sans demander une réécriture vision.

    La sélection finale reste du ressort de la Phase 5 : une figure n'est
    insérée que dans une section qui cite la même publication et dont elle
    illustre le contenu. Une indisponibilité du PDF visuel ne rend jamais
    l'Article Card scientifique invalide.
    """

    try:
        from services.scholar_visual_evidence_service import (
            extract_article_visual_evidence,
        )

        visuals = extract_article_visual_evidence(
            project=project,
            article=article,
            citation_label=citation_id,
            fulltext_info=fulltext_info,
        )
    except Exception as exc:
        visuals = []
        card.setdefault("evidence", {})["visual_evidence_error"] = (
            f"{type(exc).__name__}: {exc}"
        )
    card["visual_evidence"] = visuals
    evidence = card.setdefault("evidence", {})
    evidence["visual_evidence_available"] = bool(visuals)
    evidence["visual_evidence_count"] = len(visuals)
    evidence["visual_evidence_policy"] = (
        "original_figure_caption_provenance_no_vision_rewrite"
    )
    return card


def _build_template_field_from_sections(
    sections: Dict[str, str],
    field: str,
    abstract: str,
) -> str:
    if field == "apport":
        text = _first_non_empty(
            sections.get("abstract", ""),
            sections.get("introduction", ""),
            abstract,
        )
        return _limit_sentences(text, 4, 850)

    if field == "methode":
        text = _first_non_empty(
            sections.get("method", ""),
            sections.get("experiments", ""),
            abstract,
        )
        return _limit_sentences(text, 4, 900)

    if field == "resultat":
        text = _first_non_empty(
            sections.get("results", ""),
            sections.get("experiments", ""),
            sections.get("conclusion", ""),
        )
        return _limit_sentences(text, 4, 900)

    if field == "limites":
        text = _first_non_empty(
            sections.get("limitations", ""),
            sections.get("conclusion", ""),
            sections.get("discussion", ""),
        )
        if text:
            return _limit_sentences(text, 3, 800)

        return "Non explicitement indiqué dans le texte extrait."

    return ""


def _build_citation_exploitable(
    article: Article,
    authors: List[str],
    apport: str,
) -> str:
    label = _author_label(authors)
    year = article.year or "s.d."

    clean_apport = _limit_sentences(apport, 1, 260)

    if not clean_apport:
        clean_apport = "présentent une contribution scientifique en lien avec le verrou étudié"

    return f"{label} ({year}) {clean_apport}"


def _extract_keywords(article: Article, abstract: str, sections: Dict[str, str]) -> List[str]:
    """
    Extraction initiale non bloquante.
    Les vrais termes scientifiques sont ensuite enrichis par SciBERT dans
    enrich_card_with_scientific_entities(). Ici on conserve uniquement les
    métadonnées source déjà présentes.
    """
    sj = _as_dict(getattr(article, "source_json", None))
    raw_keywords = sj.get("keywords") or sj.get("fieldsOfStudy") or sj.get("concepts")

    keywords: List[str] = []

    if isinstance(raw_keywords, list):
        for item in raw_keywords:
            if isinstance(item, str):
                keywords.append(item)
            elif isinstance(item, dict):
                keywords.append(item.get("display_name") or item.get("name") or "")

    clean: List[str] = []
    seen = set()

    for k in keywords:
        k = _safe_text(k, 80).strip(" -_:;,.()[]{}")
        if not k:
            continue
        if _is_generic_scientific_candidate(k):
            continue
        key = _norm_key(k)
        if not key or key in seen:
            continue
        seen.add(key)
        clean.append(k)

    return clean[:12]


def _build_card_template(
    citation_id: str,
    article: Article,
    fulltext_info: Dict[str, Any],
    fast: bool = False,
) -> Dict[str, Any]:
    source_json = _as_dict(getattr(article, "source_json", None))
    full_text = fulltext_info.get("text") or ""
    abstract = _extract_abstract(article, full_text)
    sections = extract_sections_smart(full_text, abstract_hint=abstract)
    parsed_sections_debug = parse_document_sections(full_text, abstract_hint=abstract)

    authors = _extract_authors(article)

    apport = _build_template_field_from_sections(sections, "apport", abstract)
    methode = _build_template_field_from_sections(sections, "methode", abstract)
    resultat = _build_template_field_from_sections(sections, "resultat", abstract)
    limites = _build_template_field_from_sections(sections, "limites", abstract)
    citation = _build_citation_exploitable(article, authors, apport)

    card = {
        "citation_id": citation_id,
        "citation_label": citation_id,
        "article_id": article.id,
        "title": article.title,
        "authors": authors,
        "year": article.year,
        "source": article.source,
        "doi": article.doi,
        "url": article.url,
        "role": article.tag_article or "Non classé",
        "tag": article.tag_article or "Non classé",
        "score": article.score,
        "guided_research_source": bool(
            source_json.get("guided_research_source")
        ),
        "guided_candidate_id": source_json.get("guided_candidate_id"),
        "consultant_evidence_role": source_json.get(
            "consultant_evidence_role"
        ),
        "candidate_kind": (
            source_json.get("candidate_kind")
            or (
                "scientific_article"
                if source_json.get("guided_research_source")
                else None
            )
        ),
        "section_ids": list(source_json.get("section_ids") or []),
        "target_verrous": list(
            source_json.get("target_verrous")
            or source_json.get("covered_verrou_ids")
            or []
        ),
        "verrou_ids": list(
            source_json.get("target_verrous")
            or source_json.get("covered_verrou_ids")
            or []
        ),

        "abstract": _limit_sentences(abstract, 6, 3500),

        "objectif": _limit_sentences(abstract, 2, 600),
        "probleme_scientifique": _limit_sentences(sections.get("introduction") or abstract, 3, 800),
        "apport_scientifique": apport,
        "methode": methode,
        "method": methode,
        "jeu_de_donnees": _limit_sentences(sections.get("experiments", ""), 2, 550),
        "evaluation": _limit_sentences(sections.get("experiments", ""), 2, 550),
        "resultat": resultat,
        "results": resultat,
        "contribution": apport,
        "limites": limites,
        "limitations": limites,
        "limite_pour_notre_projet": limites,
        "travaux_futurs": _limit_sentences(sections.get("conclusion", ""), 2, 550),
        "citation_exploitable": citation,
        "mots_cles": _extract_keywords(article, abstract, sections),

        "sections": {
            "abstract": _safe_text(sections.get("abstract"), 1200),
            "method": _safe_text(sections.get("method"), 1600),
            "experiments": _safe_text(sections.get("experiments"), 1200),
            "results": _safe_text(sections.get("results"), 1200),
            "limitations": _safe_text(sections.get("limitations"), 1200),
            "conclusion": _safe_text(sections.get("conclusion"), 1200),
        },

        "evidence": {
            "full_text_available": bool(fulltext_info.get("found")),
            "full_text_path": fulltext_info.get("path"),
            "full_text_source_kind": fulltext_info.get("source_kind"),
            "pages_count": fulltext_info.get("pages_count"),
            "text_chars": fulltext_info.get("text_chars"),
            "text_words": fulltext_info.get("text_words"),
            "generation_mode": "template",
            "detected_headings": parsed_sections_debug.get("detected_headings", []),
            "headings_count": parsed_sections_debug.get("headings_count", 0),
            "document_type": parsed_sections_debug.get("document_type", "article"),
            "candidates_info": fulltext_info.get("candidates_info", []),
            "fulltext_provenance": fulltext_info.get("fulltext_provenance") or {},
            "fulltext_diagnostics": fulltext_info.get("fulltext_diagnostics") or {},
        },
    }

    card = enrich_card_with_scientific_entities(
        card=card,
        article=article,
        abstract=abstract,
        sections=sections,
        full_text=full_text,
        use_model=not fast,
    )

    card = apply_limitation_layer(
        card=card,
        article=article,
        authors=authors,
        sections=sections,
        full_text=full_text,
        use_llm=False,
    )

    card = apply_technical_method_layer(
        card=card,
        article=article,
        authors=authors,
        sections=sections,
        full_text=full_text,
        use_llm=False,
    )

    card = apply_extractive_article_reading_layer(
        card=card,
        article=article,
        sections=sections,
        full_text=full_text,
    )

    if EXTRACTIVE_ONLY_PHASE2:
        card.setdefault("evidence", {})["generation_mode"] = "extractive_original_paragraphs"

    card["quality_guard"] = validate_article_card(card)

    return card


# ============================================================
# LLM card builder
# ============================================================

def _scientific_passages_to_text(scientific_context: Optional[Dict[str, Any]], max_chars: int = 2800) -> str:
    """Convertit les passages SciBERT en bloc court et exploitable par le LLM."""
    if not isinstance(scientific_context, dict):
        return ""

    lines: List[str] = []

    passages = scientific_context.get("key_passages") or []
    for item in passages[:10]:
        term = _safe_text(item.get("term"), 80)
        usage = _safe_text(item.get("usage"), 80)
        section = _safe_text(item.get("section"), 50)
        sent = _safe_text(item.get("sentence"), 420)
        if not sent:
            continue
        prefix = f"- [{section}]"
        if term:
            prefix += f" {term}"
        if usage:
            prefix += f" ({usage})"
        lines.append(f"{prefix}: {sent}")

    # Si SciBERT n'a pas produit assez de passages, on ajoute les meilleurs termes
    # avec leurs phrases sources.
    if len(lines) < 5:
        entities = scientific_context.get("technical_entities") or []
        seen = {_norm_key(x) for x in lines}
        for ent in entities[:8]:
            term = _safe_text(ent.get("term"), 80)
            usage = _safe_text(ent.get("usage"), 80)
            sections = ent.get("sections") or []
            section = _safe_text(sections[0] if sections else "unknown", 50)
            sents = ent.get("source_sentences") or []
            sent = _safe_text(sents[0] if sents else "", 420)
            if not term or not sent:
                continue
            line = f"- [{section}] {term} ({usage}): {sent}"
            k = _norm_key(line)
            if k in seen:
                continue
            seen.add(k)
            lines.append(line)
            if len(lines) >= 10:
                break

    return _safe_text("\n".join(lines), max_chars)


def _build_llm_context(
    article: Article,
    abstract: str,
    sections: Dict[str, str],
    limitation_evidence: Optional[Dict[str, Any]] = None,
    scientific_context: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Contexte LLM V2.6 : on n'envoie plus un gros bloc naïf.

    Ordre logique :
    1) SciBERT extrait les termes et passages clés.
    2) On construit un contexte court : abstract + méthode + résultats + limites + conclusion + passages SciBERT.
    3) Le LLM rédige la fiche à partir de ces éléments, sans devoir lire tout l'article.
    """
    limitation_text = _limitation_evidence_to_text(limitation_evidence or {}, max_chars=2600) if limitation_evidence else ""
    scientific_passages_text = _scientific_passages_to_text(scientific_context, max_chars=3000)

    parts = [
        f"TITRE: {article.title}",
        f"ANNEE: {article.year}",
        f"ROLE: {article.tag_article}",
        f"DOI: {article.doi}",
        f"URL: {article.url}",
        "\nABSTRACT:",
        _safe_text(abstract, 2200),
        "\nMETHODE / PRINCIPE TECHNIQUE:",
        _safe_text(sections.get("method"), 3600),
        "\nRESULTATS / EVALUATION:",
        _safe_text(sections.get("results") or sections.get("experiments"), 2600),
        "\nLIMITES / CONTRAINTES / PERSPECTIVES A UTILISER SANS INVENTER:",
        limitation_text,
        "\nCONCLUSION:",
        _safe_text(sections.get("conclusion"), 1600),
        "\nPASSAGES_SCIENTIFIQUES_CLES_SCI_BERT:",
        scientific_passages_text or "Aucun passage SciBERT exploitable.",
    ]

    context = "\n".join([p for p in parts if p is not None])
    return _safe_text(context, MAX_CONTEXT_CHARS)

def _build_card_llm(
    citation_id: str,
    article: Article,
    fulltext_info: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    full_text = fulltext_info.get("text") or ""

    if not full_text or len(full_text) < MIN_USEFUL_FULLTEXT_CHARS:
        return None

    abstract = _extract_abstract(article, full_text)
    sections = extract_sections_smart(full_text, abstract_hint=abstract)
    parsed_sections_debug = parse_document_sections(full_text, abstract_hint=abstract)
    authors = _extract_authors(article)
    limitation_evidence = extract_limitation_evidence(sections, full_text)

    # SciBERT passe AVANT le LLM pour sélectionner les passages utiles.
    scientific_context = extract_scientific_entities(article, abstract, sections, full_text=full_text)

    context = _build_llm_context(
        article,
        abstract,
        sections,
        limitation_evidence=limitation_evidence,
        scientific_context=scientific_context,
    )

    author_label = _author_label(authors)
    year = article.year or "s.d."

    prompt = f"""
Tu es un assistant scientifique spécialisé dans les états de l'art CIR.

Ta tâche : générer UNE fiche article fiable à partir du texte fourni.

Objectif V3 :
La fiche ne doit pas seulement résumer l'article. Elle doit extraire une compréhension technique profonde,
capable d'alimenter une rédaction naturelle de consultant.

Règles strictes :
- Réponds uniquement avec un JSON valide.
- N'invente jamais.
- N'ajoute pas de chiffres absents du texte.
- Ne traduis pas les noms propres, acronymes, datasets, métriques et noms de méthodes.
- Ne copie pas tout l'abstract.
- Si une information est absente, écris exactement : "Non explicitement indiqué dans le texte extrait."
- Pour les champs "limites", "travaux_futurs" et "limite_pour_notre_projet", utilise uniquement les extraits fournis dans LIMITES / CONTRAINTES / PERSPECTIVES.
- Utilise PASSAGES_SCIENTIFIQUES_CLES_SCI_BERT pour identifier les termes techniques importants, mais ne les transforme jamais en preuve sans phrase source.
- Si aucune limite explicite n'est présente, écris : "Les auteurs ne mentionnent pas explicitement de limitation."
- Ne crée jamais une limite plausible non présente dans les extraits.
- La citation exploitable doit être une phrase courte, maximum 40 mots, directement utilisable dans un état de l'art.
- La citation exploitable doit commencer par "{author_label} ({year})".
- Le champ technical_narrative_capsule doit expliquer le mécanisme comme une histoire technique : données → transformation/modèle → entraînement/validation → résultat → limite.
- La chaîne "technical_mechanism_chain" doit contenir des étapes concrètes dans l'ordre logique, pas une liste de mots-clés.

JSON attendu exactement :
{{
  "objectif": "...",
  "probleme_scientifique": "...",
  "apport_scientifique": "...",
  "methode": "...",
  "jeu_de_donnees": "...",
  "evaluation": "...",
  "resultat": "...",
  "contribution": "...",
  "limites": "...",
  "travaux_futurs": "...",
  "limite_pour_notre_projet": "...",
  "citation_exploitable": "...",
  "mots_cles": ["...", "..."],
  "technical_narrative_capsule": {{
    "problem_to_solve": "...",
    "technical_principle_explained": "...",
    "technical_mechanism_chain": ["étape 1", "étape 2", "étape 3"],
    "data_flow": "...",
    "model_or_algorithm_flow": "...",
    "validation_flow": "...",
    "demonstrated_result": "...",
    "non_demonstrated_or_limits": "...",
    "transposition_to_project": "...",
    "consultant_explanation_seed": "..."
  }}
}}

TEXTE ARTICLE :
{context}
""".strip()

    llm_json = _call_ollama_json(prompt, task=f"main_card_article_{article.id}")

    if not llm_json:
        return None

    base = _build_card_template(citation_id, article, fulltext_info)

    text_keys = [
        "objectif",
        "probleme_scientifique",
        "apport_scientifique",
        "methode",
        "jeu_de_donnees",
        "evaluation",
        "resultat",
        "contribution",
        "limites",
        "travaux_futurs",
        "limite_pour_notre_projet",
        "citation_exploitable",
    ]

    for key in text_keys:
        value = clean_article_text(_safe_text(llm_json.get(key), 1700))
        if value:
            base[key] = value

    raw_capsule = llm_json.get("technical_narrative_capsule")
    if isinstance(raw_capsule, dict) and DEEP_TECHNICAL_READING_ENABLED:
        template_capsule = _template_technical_narrative_capsule(
            article=article,
            sections=sections,
            card=base,
            full_text=full_text,
        )
        base["technical_narrative_capsule"] = _normalize_technical_narrative_capsule(
            raw_capsule,
            fallback=template_capsule,
        )

    base["method"] = base.get("methode")
    base["results"] = base.get("resultat")
    base["limitations"] = base.get("limites")

    mots_cles = llm_json.get("mots_cles")
    if isinstance(mots_cles, list):
        llm_terms = [_safe_text(x, 80) for x in mots_cles if _safe_text(x, 80)]
        entity_terms = [
            _safe_text(x.get("term"), 80)
            for x in base.get("scientific_entities", [])
            if _safe_text(x.get("term"), 80)
        ]
        merged_terms: List[str] = []
        seen_terms = set()
        for term in llm_terms + entity_terms:
            key = _norm_key(term)
            if not key or key in seen_terms or _is_generic_scientific_candidate(term):
                continue
            seen_terms.add(key)
            merged_terms.append(term)
            if len(merged_terms) >= 18:
                break
        base["mots_cles"] = merged_terms

    if not base.get("resultat"):
        base["resultat"] = "Non explicitement indiqué dans le texte extrait."

    if not base.get("limite_pour_notre_projet"):
        base["limite_pour_notre_projet"] = base.get("limites") or "Non explicitement indiqué dans le texte extrait."

    # Optimisation V3.1 :
    # - adaptive : comportement recommandé pour tous les projets.
    #   Le LLM principal extrait déjà la capsule narrative profonde.
    #   On lance les couches LLM supplémentaires uniquement si cette capsule est faible.
    # - core : jamais de couches supplémentaires.
    # - full : ancien mode complet, à réserver aux audits ponctuels.
    capsule_quality_score = _score_technical_narrative_capsule(base.get("technical_narrative_capsule"))
    extra_llm = _should_use_extra_llm_for_article(base, article)

    base = apply_limitation_layer(
        card=base,
        article=article,
        authors=authors,
        sections=sections,
        full_text=full_text,
        use_llm=extra_llm,
    )

    base = apply_technical_method_layer(
        card=base,
        article=article,
        authors=authors,
        sections=sections,
        full_text=full_text,
        use_llm=extra_llm,
    )

    base = apply_extractive_article_reading_layer(
        card=base,
        article=article,
        sections=sections,
        full_text=full_text,
    )

    base["evidence"]["generation_mode"] = "llm_core_deep" if not extra_llm else "llm_full_deep"
    base["evidence"]["llm_strategy"] = LLM_STRATEGY
    base["evidence"]["adaptive_extra_llm_used"] = bool(extra_llm and LLM_STRATEGY == "adaptive")
    base["evidence"]["technical_capsule_quality_score"] = capsule_quality_score
    base["evidence"]["technical_capsule_min_score"] = ADAPTIVE_CAPSULE_MIN_SCORE
    base["evidence"]["llm_model"] = OLLAMA_MODEL
    base["evidence"]["deep_technical_reading_enabled"] = bool(DEEP_TECHNICAL_READING_ENABLED)
    base["evidence"]["detected_headings"] = parsed_sections_debug.get("detected_headings", [])
    base["evidence"]["headings_count"] = parsed_sections_debug.get("headings_count", 0)
    base["evidence"]["document_type"] = parsed_sections_debug.get("document_type", "article")
    base["quality_guard"] = validate_article_card(base)

    return base



# ============================================================
# Quality guard
# ============================================================

def _too_similar(a: str, b: str) -> bool:
    a = _safe_text(a).lower()
    b = _safe_text(b).lower()

    if not a or not b:
        return False

    if len(a) > 800 and a[:600] in b:
        return True

    if len(b) > 800 and b[:600] in a:
        return True

    aw = set(re.findall(r"\w{4,}", a))
    bw = set(re.findall(r"\w{4,}", b))

    if not aw or not bw:
        return False

    overlap = len(aw & bw) / max(1, min(len(aw), len(bw)))
    return overlap > 0.82 and min(len(a), len(b)) > 500


def validate_article_card(card: Dict[str, Any]) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []

    required = [
        "title",
        "apport_scientifique",
        "methode",
        "resultat",
        "limite_pour_notre_projet",
        "citation_exploitable",
    ]

    for key in required:
        value = _safe_text(card.get(key))
        if not value:
            errors.append(f"missing_{key}")

    abstract = _safe_text(card.get("abstract"))

    for key in ["apport_scientifique", "methode", "resultat", "citation_exploitable"]:
        value = _safe_text(card.get(key))
        if value and abstract and _too_similar(value, abstract):
            warnings.append(f"{key}_looks_like_abstract_copy")

    if _has_mojibake(json.dumps(card, ensure_ascii=False)):
        warnings.append("possible_mojibake_remaining")

    citation = _safe_text(card.get("citation_exploitable"))
    if _word_count(citation) > 45:
        warnings.append("citation_too_long")

    if "[URL" in citation or "Disponible à" in citation:
        warnings.append("bad_citation_format")

    evidence = _as_dict(card.get("evidence"))

    if evidence.get("full_text_available") and int(evidence.get("text_chars") or 0) < MIN_USEFUL_FULLTEXT_CHARS:
        errors.append("full_text_available_but_text_too_short")

    if not card.get("authors"):
        warnings.append("missing_authors")

    if not card.get("year"):
        warnings.append("missing_year")

    technical = _as_dict(card.get("technical_method_analysis"))
    if not technical:
        warnings.append("missing_technical_method_analysis")
    else:
        for key in ["method_name", "technical_principle", "mechanism", "concept_limits", "impact_on_verrou"]:
            if not technical.get(key):
                warnings.append(f"technical_missing_{key}")

    capsule = _as_dict(card.get("technical_narrative_capsule"))
    if not capsule:
        warnings.append("missing_technical_narrative_capsule")
    else:
        chain = capsule.get("technical_mechanism_chain") or []
        if not isinstance(chain, list) or len(chain) < 2:
            warnings.append("technical_capsule_chain_too_short")
        if not _safe_text(capsule.get("consultant_explanation_seed")):
            warnings.append("missing_consultant_explanation_seed")
        if not _safe_text(capsule.get("validation_flow")):
            warnings.append("missing_validation_flow")
        if not _safe_text(capsule.get("non_demonstrated_or_limits")):
            warnings.append("missing_non_demonstrated_or_limits")

    status = "valid" if not errors else "invalid"

    if warnings and not errors:
        status = "valid_with_warnings"

    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "checked_at": datetime.utcnow().isoformat(),
    }


def _build_citation_exploitable_from_values(label: str, year: Any, source_text: str) -> str:
    phrase = _limit_sentences(source_text, 1, 260)

    if not phrase:
        phrase = "présentent une contribution scientifique en lien avec le verrou étudié"

    return f"{label} ({year}) {phrase}"


def _repair_card_after_guard(card: Dict[str, Any]) -> Dict[str, Any]:
    authors = card.get("authors") or []
    label = _author_label(authors)
    year = card.get("year") or "s.d."

    citation = _safe_text(card.get("citation_exploitable"))

    if (
        not citation
        or "[URL" in citation
        or "Disponible à" in citation
        or _word_count(citation) > 45
    ):
        source = card.get("apport_scientifique") or card.get("resultat") or card.get("abstract") or ""
        card["citation_exploitable"] = _build_citation_exploitable_from_values(label, year, source)

    if not _safe_text(card.get("resultat")):
        card["resultat"] = "Non explicitement indiqué dans le texte extrait."

    if not _safe_text(card.get("limite_pour_notre_projet")):
        card["limite_pour_notre_projet"] = (
            card.get("limites")
            or "Non explicitement indiqué dans le texte extrait."
        )

    technical = _as_dict(card.get("technical_method_analysis"))
    capsule = _as_dict(card.get("technical_narrative_capsule"))

    if not technical:
        card["technical_method_analysis"] = {
            "method_name": card.get("method_name") or "Non explicitement nommé",
            "technical_family": card.get("technical_family") or "concept ou méthode scientifique extrait du texte",
            "technical_principle": card.get("methode") or "Non explicitement indiqué dans le texte extrait.",
            "mechanism": card.get("methode") or "Non explicitement indiqué dans le texte extrait.",
            "concept_limits": [card.get("limite_pour_notre_projet") or "Non explicitement indiqué dans le texte extrait."],
            "transposability_limits": [card.get("limite_pour_notre_projet") or "Non explicitement indiqué dans le texte extrait."],
            "impact_on_verrou": "La limite du concept maintient une incertitude sur la levée directe du verrou.",
            "remaining_uncertainty": "Une validation spécifique au projet reste nécessaire.",
            "cir_exploitation": "À utiliser comme appui scientifique nécessitant des travaux R&D complémentaires.",
            "source": "repair_fallback",
        }

    if not capsule:
        card["technical_narrative_capsule"] = _normalize_technical_narrative_capsule(
            {},
            fallback={
                "problem_to_solve": card.get("probleme_scientifique"),
                "technical_principle_explained": card.get("technical_principle") or card.get("methode"),
                "technical_mechanism_chain": _clean_list_of_text([card.get("methode"), card.get("evaluation"), card.get("resultat")], 5, 600),
                "data_flow": card.get("jeu_de_donnees"),
                "model_or_algorithm_flow": card.get("mechanism") or card.get("methode"),
                "validation_flow": card.get("evaluation"),
                "demonstrated_result": card.get("resultat"),
                "non_demonstrated_or_limits": card.get("limite_pour_notre_projet"),
                "transposition_to_project": card.get("impact_on_verrou") or card.get("cir_exploitation"),
                "consultant_explanation_seed": " ".join(
                    _clean_list_of_text(
                        [
                            card.get("probleme_scientifique"),
                            card.get("methode"),
                            card.get("evaluation"),
                            card.get("limite_pour_notre_projet"),
                        ],
                        5,
                        600,
                    )
                ),
                "source": "repair_fallback",
            },
        )

    # Synchronise les champs plats si la réparation a créé une capsule.
    cap = _as_dict(card.get("technical_narrative_capsule"))
    card.setdefault("technical_mechanism_chain", cap.get("technical_mechanism_chain") or [])
    card.setdefault("data_flow", cap.get("data_flow"))
    card.setdefault("model_or_algorithm_flow", cap.get("model_or_algorithm_flow"))
    card.setdefault("validation_flow", cap.get("validation_flow"))
    card.setdefault("consultant_explanation_seed", cap.get("consultant_explanation_seed"))
    card.setdefault("non_demonstrated_or_limits", cap.get("non_demonstrated_or_limits"))
    card.setdefault("transposition_to_project", cap.get("transposition_to_project"))

    card["quality_guard"] = validate_article_card(card)
    return card



# ============================================================
# Public build functions
# ============================================================

def _effective_card_mode(mode: str = "auto") -> str:
    requested = (mode or CARD_MODE or "auto").lower()

    if requested in {"instant", "fast", "template_fast"}:
        return "template"

    if EXTRACTIVE_ONLY_PHASE2 or DISABLE_LLM or LLM_STRATEGY in {"off", "none", "template", "extractive", "no_llm", "paragraphs", "evidence_only"}:
        return "template"

    # Le frontend peut envoyer mode="auto". On garde le LLM, mais avec la stratégie optimisée.
    if requested in {"auto", "llm_fast", "llm_core"}:
        return "llm"

    if requested in {"llm", "template"}:
        return requested

    return "llm"


def _card_uses_mcp_fulltext(card: Dict[str, Any]) -> bool:
    """Détecte les anciennes cartes alimentées par le MCP.

    Le test direct ne doit jamais réutiliser une carte dont le texte venait
    d'un résolveur externe, même si la carte est techniquement valide.
    """
    evidence = _as_dict(card.get("evidence"))
    provenance = _as_dict(evidence.get("fulltext_provenance"))
    diagnostics = _as_dict(evidence.get("fulltext_diagnostics"))

    return any(
        bool(value)
        for value in (
            card.get("retrieved_via_mcp"),
            evidence.get("retrieved_via_mcp"),
            provenance.get("retrieved_via_mcp"),
            diagnostics.get("mcp_called"),
            diagnostics.get("mcp_status"),
            diagnostics.get("mcp_resolver_version"),
        )
    )


def _fulltext_pipeline(value: Dict[str, Any]) -> str:
    diagnostics = _as_dict(value.get("fulltext_diagnostics"))
    return _safe_text(diagnostics.get("pipeline") or value.get("pipeline"), 160)


def _existing_card_is_reusable(card: Dict[str, Any], project: Project, article: Article) -> Tuple[bool, str]:
    if not SMART_REUSE:
        return False, "smart_reuse_disabled"

    if not isinstance(card, dict):
        return False, "missing_card_json"

    if int(card.get("article_id") or 0) != int(article.id):
        return False, "article_id_mismatch"

    qg = _as_dict(card.get("quality_guard"))
    q_status = str(qg.get("status") or "").lower()
    if q_status not in REUSABLE_CARD_STATUSES:
        return False, f"quality_status_not_reusable:{q_status or 'missing'}"

    if card.get("status") == "card_generation_error":
        return False, "card_generation_error"

    if DEEP_TECHNICAL_READING_ENABLED and not card.get("technical_narrative_capsule"):
        return False, "missing_v3_technical_narrative_capsule"

    if EXTRACTIVE_READING_ENABLED and not card.get("article_evidence_bank"):
        return False, "missing_v3_2_article_evidence_bank"

    # Vérifie que le texte intégral n'a pas changé depuis la dernière carte.
    current_fulltext = _load_fulltext(project, article)
    evidence = _as_dict(card.get("evidence"))

    current_found = bool(current_fulltext.get("found"))
    old_found = bool(evidence.get("full_text_available"))

    if current_found and not old_found:
        return False, "new_fulltext_available"

    if old_found and not current_found:
        return False, "previous_fulltext_no_longer_available"

    old_pipeline = _fulltext_pipeline(evidence)
    current_pipeline = _fulltext_pipeline(current_fulltext)
    if old_pipeline and current_pipeline and old_pipeline != current_pipeline:
        return False, f"fulltext_pipeline_changed:{old_pipeline}->{current_pipeline}"

    if current_found:
        old_chars = int(evidence.get("text_chars") or 0)
        new_chars = int(current_fulltext.get("text_chars") or 0)
        if old_chars <= 0:
            return False, "old_text_chars_missing"
        if abs(old_chars - new_chars) > max(300, int(new_chars * 0.03)):
            return False, f"fulltext_changed:{old_chars}->{new_chars}"

    return True, "valid_existing_card"


def _load_reusable_card(
    project: Project,
    article: Article,
    citation_id: str,
    scope_id: str | None = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    source_json = _as_dict(article.source_json)
    saved = (
        _as_dict(source_json.get("article_card"))
        if scope_id is None
        else _as_dict(_as_dict(source_json.get("article_cards_by_scope")).get(str(scope_id)))
    )
    ok, reason = _existing_card_is_reusable(saved or {}, project, article)

    if not ok or not saved:
        return None, reason

    # Si l'ordre de citation change, on garde la carte mais on met à jour A1/A2/A3.
    saved["citation_id"] = citation_id
    saved["citation_label"] = citation_id
    saved.setdefault("evidence", {})["reused_existing_card"] = True
    saved.setdefault("evidence", {})["reuse_reason"] = reason
    saved.setdefault("evidence", {})["reused_at"] = datetime.utcnow().isoformat()
    return saved, reason


def is_article_ready_for_writing(
    fulltext_info: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    Autorise une Article Card pour la rédaction uniquement lorsqu'un texte
    intégral réellement extrait et vérifié est disponible.

    Les abstracts, métadonnées, URL trouvées ou PDF non extraits ne suffisent pas.
    """
    if not isinstance(fulltext_info, dict):
        return False, "invalid_fulltext_info"

    if fulltext_info.get("found") is not True:
        return False, "fulltext_missing"

    text = _safe_text(fulltext_info.get("text"))
    text_chars = int(fulltext_info.get("text_chars") or len(text))

    if not text or text_chars < MIN_USEFUL_FULLTEXT_CHARS:
        return False, "fulltext_too_short"

    source_kind = _safe_text(fulltext_info.get("source_kind"), 40).lower()
    if source_kind not in {"uploaded", "direct", "legal", "saved_pdf"}:
        return False, f"unsupported_fulltext_source:{source_kind or 'unknown'}"

    return True, "fulltext_verified_and_extracted"


def is_article_card_ready_for_writing(card: Dict[str, Any]) -> Tuple[bool, str]:
    """Garde-fou public utilisé aussi par l'orchestrateur."""
    if not isinstance(card, dict):
        return False, "invalid_card"

    if card.get("status") == "card_generation_error":
        return False, "card_generation_error"

    evidence = _as_dict(card.get("evidence"))
    if evidence.get("full_text_available") is not True:
        return False, "fulltext_missing"

    if int(evidence.get("text_chars") or 0) < MIN_USEFUL_FULLTEXT_CHARS:
        return False, "fulltext_too_short"

    quality_status = _safe_text(_as_dict(card.get("quality_guard")).get("status")).lower()
    if quality_status not in REUSABLE_CARD_STATUSES:
        return False, f"card_quality_not_usable:{quality_status or 'missing'}"

    return True, "ready_for_writing"


def build_article_card(
    citation_id: str,
    article: Article,
    project: Project,
    mode: str = "auto",
) -> Dict[str, Any]:
    fulltext_info = _load_fulltext(project, article)
    selected_mode = _effective_card_mode(mode)
    instant_mode = str(mode or "").strip().lower() in {"instant", "fast", "template_fast"}

    print(
        f"[EnnoScholar][ArticleCards] BUILD article_id={article.id} citation={citation_id} "
        f"mode_requested={mode} mode_effective={selected_mode} strategy={LLM_STRATEGY} "
        f"fulltext_found={bool(fulltext_info.get('found'))} text_chars={fulltext_info.get('text_chars')}",
        flush=True,
    )

    if selected_mode == "llm":
        card = _build_card_llm(citation_id, article, fulltext_info)
        if card:
            return _repair_card_after_guard(card)

        fallback = _build_card_template(citation_id, article, fulltext_info, fast=instant_mode)
        fallback["evidence"]["generation_mode"] = "template_fallback_after_llm_fail"
        fallback["evidence"]["llm_strategy"] = LLM_STRATEGY
        return _repair_card_after_guard(fallback)

    card = _build_card_template(citation_id, article, fulltext_info, fast=instant_mode)
    card["evidence"]["generation_mode"] = "extractive_original_paragraphs" if EXTRACTIVE_ONLY_PHASE2 else "template"
    card["evidence"]["llm_strategy"] = LLM_STRATEGY
    return _repair_card_after_guard(card)


def build_article_cards_for_selected_articles(
    db: Session,
    project: Project,
    mode: str = "auto",
    force: bool = False,
    *,
    scholar_run_id: int | None = None,
    scope_id: str | None = None,
) -> Dict[str, Any]:
    started = time.time()
    storage_run = _current_scholar_run_for_cards(db, project)
    if storage_run is None:
        raise RuntimeError("Aucun ScholarRun courant pour construire les Article Cards.")
    out_payload = _article_cards_db_uri(int(storage_run.id), scope_id)
    mode_effective = _effective_card_mode(mode)

    print(
        "=" * 90 + "\n"
        f"[EnnoScholar][ArticleCards] START project_id={project.id} "
        f"mode_requested={mode} mode_effective={mode_effective} force={force} "
        f"strategy={LLM_STRATEGY} smart_reuse={SMART_REUSE} disable_llm={DISABLE_LLM}\n"
        + "=" * 90,
        flush=True,
    )

    if scholar_run_id is not None:
        articles = (
            db.query(Article)
            .filter(Article.scholar_run_id == int(scholar_run_id))
            .filter(Article.consultant_status == "garde")
            .order_by(Article.score.desc(), Article.year.desc(), Article.created_at.asc())
            .all()
        )
    else:
        articles = get_selected_articles_for_project(db, project)
    print(f"[EnnoScholar][ArticleCards] SELECTED articles={len(articles)}", flush=True)

    cards: List[Dict[str, Any]] = []
    excluded_articles: List[Dict[str, Any]] = []
    reused_count = 0
    rebuilt_count = 0
    errors_count = 0

    for idx, article in enumerate(articles, start=1):
        citation_id = f"A{idx}"
        item_started = time.time()

        if isinstance(article.source_json, dict) and article.source_json.get(
            "manual_upload_source"
        ):
            try:
                from services.scholar_uploaded_pdf_extractor import (
                    normalize_existing_uploaded_article_identity,
                )

                normalize_existing_uploaded_article_identity(db, project, article)
            except ImportError as exc:
                # La normalisation d'identité est utile mais ne doit pas rendre
                # impossible la reconstruction d'une carte déjà extraite dans
                # un worker minimal dépourvu des dépendances HTTP de l'API.
                print(
                    "[EnnoScholar][ArticleCards][WARN] Normalisation upload "
                    f"indisponible article_id={article.id}: {exc}",
                    flush=True,
                )

        print(
            f"[EnnoScholar][ArticleCards] [{idx}/{len(articles)}] {citation_id} "
            f"article_id={article.id} title={_safe_text(article.title, 120)}",
            flush=True,
        )

        # Règle stricte : aucune carte scientifique n'est créée depuis les seules
        # métadonnées ou l'abstract. Sans texte intégral vérifié, l'article reste
        # sélectionné en base mais il est exclu de la rédaction.
        current_fulltext = _load_fulltext(project, article)
        writing_ready, exclusion_reason = is_article_ready_for_writing(current_fulltext)
        if not writing_ready:
            excluded = {
                "selection_citation_id": citation_id,
                "article_id": article.id,
                "title": article.title,
                "doi": article.doi,
                "url": article.url,
                "reason": exclusion_reason,
                "needs_consultant_upload": True,
                "fulltext": {
                    "found": bool(current_fulltext.get("found")),
                    "source_kind": current_fulltext.get("source_kind"),
                    "text_chars": int(current_fulltext.get("text_chars") or 0),
                    "candidates_info": current_fulltext.get("candidates_info") or [],
                    "diagnostics": current_fulltext.get("fulltext_diagnostics") or {},
                },
            }
            excluded_articles.append(excluded)
            print(
                f"[EnnoScholar][ArticleCards] [{idx}/{len(articles)}] {citation_id} "
                f"EXCLUDED_FROM_WRITING reason={exclusion_reason}",
                flush=True,
            )
            continue

        if force:
            reusable, reuse_reason = None, "force_rebuild_requested"
        else:
            reusable, reuse_reason = _load_reusable_card(
                project,
                article,
                citation_id,
                scope_id,
            )
        if reusable:
            card = reusable
            reused_count += 1
            print(
                f"[EnnoScholar][ArticleCards] [{idx}/{len(articles)}] {citation_id} "
                f"REUSE reason={reuse_reason} status={card.get('quality_guard', {}).get('status')}",
                flush=True,
            )
        else:
            rebuilt_count += 1
            print(
                f"[EnnoScholar][ArticleCards] [{idx}/{len(articles)}] {citation_id} "
                f"REBUILD reason={reuse_reason}",
                flush=True,
            )
            try:
                card = build_article_card(citation_id, article, project, mode=mode)
            except Exception as exc:
                errors_count += 1
                card = {
                    "citation_id": citation_id,
                    "citation_label": citation_id,
                    "article_id": article.id,
                    "title": article.title,
                    "authors": _extract_authors(article),
                    "year": article.year,
                    "source": article.source,
                    "doi": article.doi,
                    "url": article.url,
                    "role": article.tag_article or "Non classé",
                    "tag": article.tag_article or "Non classé",
                    "score": article.score,
                    "status": "card_generation_error",
                    "error": str(exc),
                    "quality_guard": {
                        "status": "invalid",
                        "errors": ["card_generation_error"],
                        "warnings": [],
                        "checked_at": datetime.utcnow().isoformat(),
                    },
                }

        card = _sync_card_source_context(card, article)
        card = _sync_card_visual_evidence(
            card,
            project=project,
            article=article,
            citation_id=citation_id,
            fulltext_info=current_fulltext,
        )
        cards.append(card)
        if scope_id is None:
            source_json = _as_dict(article.source_json)
            source_json["article_card"] = card
            source_json["article_card_storage"] = "database_article_source_json"
            article.source_json = source_json
            db.add(article)
        else:
            source_json = _as_dict(article.source_json)
            scoped_cards = _as_dict(source_json.get("article_cards_by_scope"))
            scoped_cards[str(scope_id)] = card
            source_json["article_cards_by_scope"] = scoped_cards
            source_json["article_card_storage"] = "database_article_source_json"
            article.source_json = source_json
            db.add(article)

        elapsed_item = round(time.time() - item_started, 2)
        print(
            f"[EnnoScholar][ArticleCards] [{idx}/{len(articles)}] {citation_id} DONE "
            f"status={card.get('quality_guard', {}).get('status')} "
            f"generation_mode={card.get('evidence', {}).get('generation_mode')} "
            f"elapsed={elapsed_item}s",
            flush=True,
        )

    db.commit()

    try:
        from services.scholar_visual_evidence_service import (
            extract_project_document_visuals,
        )

        project_visual_evidence = extract_project_document_visuals(db, project)
    except Exception as exc:
        project_visual_evidence = []
        print(
            "[EnnoScholar][ArticleCards][WARN] Figures des documents projet "
            f"indisponibles: {type(exc).__name__}: {exc}",
            flush=True,
        )

    payload = {
        "ok": True,
        "project_id": project.id,
        "scope_id": scope_id,
        "scholar_run_id": scholar_run_id,
        "selected_articles_count": len(articles),
        "cards_count": len(cards),
        "writing_ready_cards_count": len(cards),
        "excluded_from_writing_count": len(excluded_articles),
        "writing_ready_article_ids": [int(c.get("article_id")) for c in cards if c.get("article_id") is not None],
        "excluded_article_ids": [int(x.get("article_id")) for x in excluded_articles if x.get("article_id") is not None],
        "excluded_articles": excluded_articles,
        "mode": mode,
        "mode_effective": mode_effective,
        "llm_strategy": LLM_STRATEGY,
        "smart_reuse": SMART_REUSE,
        "reused_cards_count": reused_count,
        "rebuilt_cards_count": rebuilt_count,
        "errors_count": errors_count,
        "cards": cards,
        "project_visual_evidence": project_visual_evidence,
        "payload_path": str(out_payload),
        "output_path": str(out_payload),
        "generated_at": datetime.utcnow().isoformat(),
        "elapsed_seconds": round(time.time() - started, 2),
        "schema_version": "article_cards_v3_2_extractive_first_no_llm",
        "rules": {
            "article_cards_as_scientific_sources": True,
            "only_verified_fulltext_cards_sent_to_writing": True,
            "metadata_or_abstract_only_cards_forbidden": True,
            "selected_articles_without_fulltext_kept_but_excluded": True,
            "memory_v2_used_as_proof": False,
            "technical_method_analysis_enabled": True,
            "concept_limits_enabled": True,
            "impact_on_verrou_enabled": True,
            "scientific_ner_enabled": SCIENTIFIC_NER_ENABLED,
            "scientific_ner_model": SCIENTIFIC_NER_MODEL,
            "scientific_entities_used_as_candidates_only": True,
            "llm_kept_but_optimized": not EXTRACTIVE_ONLY_PHASE2,
            "deep_technical_reading_enabled": DEEP_TECHNICAL_READING_ENABLED,
            "technical_narrative_capsule_enabled": True,
            "adaptive_strategy_default": LLM_STRATEGY == "adaptive",
            "adaptive_extra_llm_only_for_weak_capsules": ADAPTIVE_EXTRA_LLM_ON_WEAK_CARD,
            "adaptive_capsule_min_score": ADAPTIVE_CAPSULE_MIN_SCORE,
            "extractive_reading_enabled": EXTRACTIVE_READING_ENABLED,
            "extractive_only_phase2": EXTRACTIVE_ONLY_PHASE2,
            "core_strategy_means_one_llm_call_per_article": LLM_STRATEGY == "core",
            "original_figures_extracted_without_vision_rewrite": True,
            "project_document_figures_are_context_not_scientific_proof": True,
        },
        "quality": {
            "selected_articles": len(articles),
            "writing_ready_cards": len(cards),
            "excluded_missing_fulltext": len(excluded_articles),
            "full_text_cards": sum(1 for c in cards if c.get("evidence", {}).get("full_text_available")),
            "template_cards": sum(1 for c in cards if "template" in c.get("evidence", {}).get("generation_mode", "")),
            "llm_cards": sum(1 for c in cards if str(c.get("evidence", {}).get("generation_mode", "")).startswith("llm")),
            "llm_core_cards": sum(1 for c in cards if c.get("evidence", {}).get("generation_mode") in {"llm_core", "llm_core_deep"}),
            "llm_full_cards": sum(1 for c in cards if c.get("evidence", {}).get("generation_mode") in {"llm_full", "llm_full_deep"}),
            "adaptive_extra_llm_cards": sum(1 for c in cards if c.get("evidence", {}).get("adaptive_extra_llm_used")),
            "valid_cards": sum(1 for c in cards if c.get("quality_guard", {}).get("status") == "valid"),
            "valid_with_warnings_cards": sum(1 for c in cards if c.get("quality_guard", {}).get("status") == "valid_with_warnings"),
            "invalid_cards": sum(1 for c in cards if c.get("quality_guard", {}).get("status") == "invalid"),
            "cards_with_technical_method_analysis": sum(1 for c in cards if c.get("technical_method_analysis")),
            "cards_with_concept_limits": sum(1 for c in cards if c.get("concept_limits")),
            "cards_with_impact_on_verrou": sum(1 for c in cards if c.get("impact_on_verrou")),
            "cards_with_technical_narrative_capsule": sum(1 for c in cards if c.get("technical_narrative_capsule")),
            "cards_with_article_evidence_bank": sum(1 for c in cards if c.get("article_evidence_bank")),
            "cards_with_consultant_explanation_seed": sum(1 for c in cards if c.get("consultant_explanation_seed") or c.get("technical_narrative_capsule", {}).get("consultant_explanation_seed")),
            "cards_with_scientific_entities": sum(1 for c in cards if c.get("scientific_entities")),
            "scientific_entities_total": sum(len(c.get("scientific_entities") or []) for c in cards),
            "cards_with_key_scientific_passages": sum(1 for c in cards if c.get("key_scientific_passages")),
            "cards_with_visual_evidence": sum(1 for c in cards if c.get("visual_evidence")),
            "article_visual_evidence_count": sum(len(c.get("visual_evidence") or []) for c in cards),
            "project_visual_evidence_count": len(project_visual_evidence),
            "cards_with_fulltext_but_empty_text": sum(
                1
                for c in cards
                if c.get("evidence", {}).get("full_text_available")
                and int(c.get("evidence", {}).get("text_chars") or 0) < MIN_USEFUL_FULLTEXT_CHARS
            ),
        },
    }

    payload = _save_article_cards_payload_to_db(
        db,
        project,
        payload,
        scope_id=scope_id,
    )
    print(
        "=" * 90 + "\n"
        f"[EnnoScholar][ArticleCards] END project_id={project.id} selected={len(articles)} "
        f"writing_ready_cards={len(cards)} excluded={len(excluded_articles)} "
        f"reused={reused_count} rebuilt={rebuilt_count} errors={errors_count} "
        f"elapsed={payload['elapsed_seconds']}s\n"
        + "=" * 90,
        flush=True,
    )
    return payload


def sync_article_cards_after_consultant_decision(
    db: Session,
    project: Project,
    article: Article,
) -> Dict[str, Any]:
    """Crée la carte à la conservation et la supprime au rejet/attente."""
    selected = str(article.consultant_status or "") == "garde"
    source_json = _as_dict(article.source_json)

    card: Dict[str, Any] | None = None
    if selected:
        evidence = _as_dict(source_json.get("evidence_preflight"))
        if evidence.get("evidence_status") == "FULLTEXT_READY":
            reusable, _ = _load_reusable_card(project, article, f"A{int(article.id)}")
            card = reusable or build_article_card(
                f"A{int(article.id)}",
                article,
                project,
                mode="instant",
            )
            card = _sync_card_source_context(card, article)
            source_json["article_card"] = card
            source_json["article_card_storage"] = "database_article_source_json"
            source_json.pop("article_card_sync_error", None)
            article.source_json = source_json
            db.add(article)
            db.commit()

    if not selected:
        source_json.pop("article_card", None)
        source_json.pop("article_card_storage", None)
        article.source_json = source_json
        db.add(article)
        db.commit()

        # Nettoyage des anciens artefacts disque ; les nouvelles cartes du
        # corpus principal sont conservées uniquement en base.
        base_dir = _article_cards_dir(project)
        if base_dir.exists():
            for path in base_dir.rglob(f"article_{int(article.id)}*_card.json"):
                if path.is_file():
                    path.unlink(missing_ok=True)

    # Maintient l'index agrégé en base sans recalculer toutes les cartes.
    current_run = _current_scholar_run_for_cards(db, project)
    raw = dict(current_run.raw_result_json or {}) if current_run is not None else {}
    payload = dict(raw.get("article_cards_payload") or {})
    cards = [
        item for item in (payload.get("cards") or [])
        if int(item.get("article_id") or 0) != int(article.id)
    ]
    if card is not None:
        cards.append(card)
    payload.update({
        "ok": True,
        "project_id": int(project.id),
        "cards": cards,
        "cards_count": len(cards),
        "writing_ready_cards_count": len(cards),
        "writing_ready_article_ids": [
            int(item.get("article_id"))
            for item in cards
            if item.get("article_id") is not None
        ],
        "generated_at": datetime.utcnow().isoformat(),
        "storage_mode": "database_only",
    })
    _save_article_cards_payload_to_db(db, project, payload)

    return {
        "ok": True,
        "decision_article_id": int(article.id),
        "decision": article.consultant_status,
        "article_card_created": card is not None,
        "article_card_deleted": not selected,
        "storage": "database_article_source_json",
    }


def get_article_cards_payload(
    project: Project,
    scope_id: str | None = None,
    db: Session | None = None,
) -> Dict[str, Any]:
    owned_session = db is None
    if db is None:
        from db.database import SessionLocal

        db = SessionLocal()
    try:
        run = _current_scholar_run_for_cards(db, project)
        if run is None:
            return {
                "ok": False,
                "project_id": project.id,
                "status": "not_built",
                "message": "Aucun run EnnoScholar courant.",
            }
        raw = dict(run.raw_result_json or {})
        if scope_id:
            saved = _as_dict(
                _as_dict(raw.get("article_cards_payload_by_scope")).get(str(scope_id))
            )
        else:
            saved = _as_dict(raw.get("article_cards_payload"))
        if saved:
            return saved

        articles = (
            db.query(Article)
            .filter(Article.scholar_run_id == int(run.id))
            .filter(Article.consultant_status == "garde")
            .order_by(Article.score.desc(), Article.year.desc(), Article.created_at.asc())
            .all()
        )
        cards = []
        for article in articles:
            source_json = _as_dict(article.source_json)
            card = (
                _as_dict(source_json.get("article_card"))
                if scope_id is None
                else _as_dict(_as_dict(source_json.get("article_cards_by_scope")).get(str(scope_id)))
            )
            if card:
                cards.append(card)
        if cards:
            return {
                "ok": True,
                "project_id": int(project.id),
                "scholar_run_id": int(run.id),
                "scope_id": scope_id,
                "cards": cards,
                "cards_count": len(cards),
                "writing_ready_cards_count": len(cards),
                "writing_ready_article_ids": [int(card["article_id"]) for card in cards],
                "storage_mode": "database_only",
                "payload_path": _article_cards_db_uri(int(run.id), scope_id),
            }
        return {
            "ok": False,
            "project_id": project.id,
            "status": "not_built",
            "message": "Les fiches articles n'ont pas encore été générées.",
            "expected_path": _article_cards_db_uri(int(run.id), scope_id),
            "storage_mode": "database_only",
        }
    finally:
        if owned_session:
            db.close()
