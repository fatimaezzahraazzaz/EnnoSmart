# -*- coding: utf-8 -*-
from __future__ import annotations

"""
phase_4_5_scientific_reasoning_builder_service.py

EnnoScholar — Phase 4.5 : Scientific Reasoning Builder

Version V2.1 — Open-domain No-loss Scientific Technical Extraction

Objectif :
- construire un raisonnement scientifique / CIR exploitable avant la rédaction LLM ;
- transformer le gap Phase 4 en logique consultant :
    littérature -> concepts/méthodes -> limites conceptuelles -> non-transposabilité -> verrou -> R&D ;
- consommer les Article Cards Phase 2 enrichies :
    technical_method_analysis
    technical_concept_limits
    concept_limits
    transposability_limits
    impact_on_verrou
    cir_exploitation
- donner plus de poids aux articles :
    proches du verrou,
    Direct,
    avec fulltext,
    avec analyse technique exploitable,
    avec qualité valide ;
- ne jamais ignorer un article sélectionné par le consultant ;
- ne pas faire de codage dur projet / domaine ;
- produire un payload structuré pour Phase 5.
- ajouter une couche de raisonnement consultant : storyline, approfondissement des concepts, comparaisons contrôlées, limites causales, démonstration R&D.

Important :
- pas d'appel LLM ;
- pas de preuve depuis Memory V2 ;
- Article Cards = seules sources scientifiques ;
- citations uniquement sous forme [A1], [A2]... ;
- pas de rédaction article-par-article ;
- les noms de méthodes/concepts sont autorisés ;
- les citations Connexes ne deviennent jamais des preuves Directes ;
- les articles faibles restent conservés, mais avec poids/priorité plus bas.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT_DIR = Path(os.getenv("ENNOSMART_ROOT_DIR", r"C:\EnnoSmart"))


# ============================================================
# Helpers généraux
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, (int, float)):
        return str(value)

    if isinstance(value, (list, tuple)):
        return " ".join(clean_text(x) for x in value if clean_text(x))

    if isinstance(value, dict):
        for key in [
            "text",
            "value",
            "label",
            "title",
            "name",
            "summary",
            "resume",
            "principle",
            "mechanism",
            "reasoning",
        ]:
            txt = clean_text(value.get(key))
            if txt:
                return txt
        return ""

    s = str(value)
    s = s.replace("\u00a0", " ")
    s = s.replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()




def clean_evidence_items(value: Any, max_items: int = 5, max_chars: int = 650) -> List[str]:
    """
    Nettoie une liste de preuves/limites extraite des Article Cards.
    - accepte str/list/dict ;
    - supprime les placeholders et textes trop faibles ;
    - déduplique ;
    - tronque prudemment ;
    - reste totalement multi-domaine.
    """
    raw_items = as_list(value)
    cleaned: List[str] = []
    seen = set()

    weak_markers = {
        "",
        "n/a",
        "na",
        "none",
        "null",
        "non renseigné",
        "non renseigne",
        "non explicitement indiqué",
        "non explicitement indique",
        "not specified",
        "not available",
        "not mentioned",
        "no limitation",
        "no limitations",
    }

    for item in raw_items:
        txt = clean_text(item)
        if not txt:
            continue

        norm = normalize_for_match(txt)
        if norm in weak_markers:
            continue

        # Évite les fragments trop courts qui ne portent pas de preuve exploitable.
        if len(norm) < 18 and len(norm.split()) < 3:
            continue

        txt = truncate(txt, max_chars=max_chars)
        key = normalize_for_match(txt)[:180]
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(txt)

        if len(cleaned) >= max_items:
            break

    return cleaned


def fs_slug(value: Any) -> str:
    s = str(value or "").strip().lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’'", "aaaeeeeiioouuuc__")
    s = s.translate(tr)
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unknown"


def _read_json(path: str | Path, default=None):
    path = Path(path)
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _write_json(path: str | Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def truncate(text: Any, max_chars: int = 900) -> str:
    s = clean_text(text)

    if len(s) <= max_chars:
        return s

    cut = s[:max_chars].rstrip()
    last_dot = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))

    if last_dot >= 180:
        return cut[:last_dot + 1].strip()

    last_space = cut.rfind(" ")
    if last_space >= 180:
        return cut[:last_space].strip()

    return cut.strip()


def normalize_for_match(text: Any) -> str:
    s = clean_text(text).lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’", "aaaeeeeiioouuuc'")
    s = s.translate(tr)
    s = re.sub(r"[^a-z0-9\s_\-]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []

    if isinstance(value, list):
        return value

    if isinstance(value, dict):
        return list(value.values())

    return [value]




# ============================================================
# Helpers anti-placeholder / impact générique
# ============================================================

PLACEHOLDER_MARKERS = [
    "non explicitement indique",
    "non explicitement indiqué",
    "non explicitement nomme",
    "non explicitement nommé",
    "les auteurs ne formulent pas de limitation explicite",
    "aucun gap de transposition",
    "aucune limitation explicite",
    "pas explicitement mentionne",
    "pas explicitement mentionné",
    "not explicitly stated",
    "not explicitly mentioned",
    "not specified",
    "not available",
    "n/a",
]

WEAK_GENERIC_IMPACT_MARKERS = [
    "ces limites maintiennent une incertitude",
    "la performance réelle, la robustesse et la généralisation doivent être vérifiées",
    "la performance reelle la robustesse et la generalisation doivent etre verifiees",
    "ce concept peut être mobilisé dans l'état de l'art comme appui scientifique",
    "il justifie encore des travaux r&d",
    "il justifie encore des travaux rd",
]




# ============================================================
# V1.9 — Technical detail extraction (no domain/citation hardcoding)
# ============================================================

TECH_DETAIL_MAX_SOURCE_CHARS = 18000


def _flatten_text_values(value: Any, *, max_depth: int = 5) -> List[str]:
    """
    Récupère récursivement les textes utiles dans les Article Cards.
    Ne dépend d'aucun domaine : on garde les textes sources, puis on extrait
    uniquement les paramètres explicitement présents.
    """
    if max_depth <= 0 or value is None:
        return []

    if isinstance(value, str):
        txt = clean_text(value)
        return [txt] if txt else []

    if isinstance(value, (int, float)):
        return [str(value)]

    if isinstance(value, list):
        out: List[str] = []
        for x in value:
            out.extend(_flatten_text_values(x, max_depth=max_depth - 1))
        return out

    if isinstance(value, dict):
        out: List[str] = []
        # Priorité aux champs qui portent souvent la preuve technique.
        priority_keys = [
            "text", "paragraph", "value", "summary", "abstract", "method", "methods",
            "methodology", "workflow", "dataset", "validation", "results", "limitations",
            "technical_principle", "principle", "mechanism", "phase2_evidence_for_reasoning",
            "problem_context", "solution_context", "dataset_context", "validation_protocol",
            "reported_results", "training_pipeline", "evaluation_protocol", "experimental_results",
        ]
        for k in priority_keys:
            if k in value:
                out.extend(_flatten_text_values(value.get(k), max_depth=max_depth - 1))
        for k, v in value.items():
            if k in priority_keys or k == "raw":
                continue
            out.extend(_flatten_text_values(v, max_depth=max_depth - 1))
        return out

    return []


def _dedup_texts(texts: List[str], *, max_items: int = 80, max_chars_each: int = 1400) -> List[str]:
    out: List[str] = []
    seen = set()
    for text in texts:
        txt = truncate(text, max_chars=max_chars_each)
        if not txt or is_placeholder_text(txt):
            continue
        key = normalize_for_match(txt)[:220]
        if key in seen:
            continue
        seen.add(key)
        out.append(txt)
        if len(out) >= max_items:
            break
    return out


def build_technical_detail_source_text(card: Dict[str, Any], technical: Dict[str, Any]) -> str:
    """
    Source unique d'extraction de détails techniques.
    On privilégie les champs extractifs Phase 2, puis les champs normalisés.
    """
    sources: List[str] = []
    for key in [
        "phase2_evidence_for_reasoning",
        "evidence_extracts_for_reasoning",
        "article_evidence_bank",
        "technical_method_analysis",
        "technical_concept_limits",
        "abstract", "method", "results", "limitations", "relevance",
        "problem_context", "solution_context", "dataset_context", "validation_protocol", "reported_results",
    ]:
        sources.extend(_flatten_text_values(card.get(key)))
    sources.extend(_flatten_text_values(technical))
    cleaned = _dedup_texts(sources)
    return truncate("\n".join(cleaned), TECH_DETAIL_MAX_SOURCE_CHARS)


def _snippet_around(text: str, start: int, end: int, *, window: int = 170) -> str:
    left = max(0, start - window)
    right = min(len(text), end + window)
    snippet = clean_text(text[left:right])
    return truncate(snippet, 420)


def extract_snippets_by_patterns(text: str, patterns: List[str], *, max_items: int = 8) -> List[str]:
    out: List[str] = []
    seen = set()
    for pat in patterns:
        try:
            matches = list(re.finditer(pat, text, flags=re.IGNORECASE | re.MULTILINE))
        except re.error:
            continue
        for m in matches:
            snip = _snippet_around(text, m.start(), m.end())
            key = normalize_for_match(snip)[:180]
            if not snip or key in seen:
                continue
            seen.add(key)
            out.append(snip)
            if len(out) >= max_items:
                return out
    return out


def extract_values_by_patterns(text: str, patterns: List[str], *, max_items: int = 10) -> List[str]:
    values: List[str] = []
    seen = set()
    for pat in patterns:
        try:
            for m in re.finditer(pat, text, flags=re.IGNORECASE | re.MULTILINE):
                value = clean_text(m.group(0))
                key = normalize_for_match(value)
                if not value or key in seen:
                    continue
                seen.add(key)
                values.append(value)
                if len(values) >= max_items:
                    return values
        except re.error:
            continue
    return values


TECHNICAL_DETAIL_PATTERNS = {
    "epochs": [
        r"\b\d{1,5}\s*(?:epochs?|époques?)\b",
        r"\b(?:epochs?|époques?)\s*[:=]\s*\d{1,5}\b",
    ],
    "batch_size": [
        r"\b(?:batch\s*size|batch-size|mini[- ]?batch)\s*[:=]?\s*\d{1,6}\b",
        r"\b\d{1,6}\s*(?:samples?|images?)\s+per\s+batch\b",
    ],
    "learning_rate": [
        r"\b(?:learning\s*rate|lr)\s*[:=]?\s*[0-9]+(?:\.[0-9]+)?(?:e[-+]?\d+)?\b",
        r"\b[0-9]+(?:\.[0-9]+)?e[-+]?\d+\s*(?:learning\s*rate|lr)\b",
    ],
    "optimizer": [
        r"\b(?:optimizer|optimiseur)\s*[:=]?\s*[A-Za-z0-9_+\-]+\b",
        r"\b(?:AdamW?|SGD|RMSprop|Adagrad|Adadelta|Nadam)\b",
    ],
    "loss_function": [
        r"\b(?:loss\s*function|loss|fonction\s+de\s+perte)\s*[:=]?\s*[A-Za-z0-9_+\- ]{3,80}",
        r"\b(?:cross[- ]?entropy|contrastive\s+loss|triplet\s+loss|focal\s+loss|mse|mean\s+squared\s+error)\b",
    ],
    "dropout": [
        r"\bdropout\s*(?:rate)?\s*[:=]?\s*[0-9]*\.?[0-9]+\b",
    ],
    "weight_decay": [
        r"\bweight\s*decay\s*[:=]?\s*[0-9]+(?:\.[0-9]+)?(?:e[-+]?\d+)?\b",
    ],
    "momentum": [
        r"\bmomentum\s*[:=]?\s*[0-9]*\.?[0-9]+\b",
    ],
    "layers": [
        r"\b\d{1,4}\s*(?:layers?|couches?)\b",
        r"\b(?:layers?|couches?)\s*[:=]?\s*\d{1,4}\b",
        r"\b(?:fully\s+connected|convolutional|conv|transformer|encoder|decoder)\s+layers?\b",
    ],
    "input_shape": [
        r"\b\d{2,5}\s*[x×]\s*\d{2,5}(?:\s*[x×]\s*\d{1,5})?\b",
        r"\binput\s*(?:image|shape|size)?\s*[:=]?\s*\d{2,5}\s*[x×]\s*\d{2,5}(?:\s*[x×]\s*\d{1,5})?\b",
    ],
    "patch_size": [
        r"\bpatch\s*size\s*[:=]?\s*\d{1,4}(?:\s*[x×]\s*\d{1,4})?\b",
        r"\b\d{1,4}\s*patches\b",
    ],
    "classes": [
        r"\b\d{1,4}\s*(?:classes|catégories|categories)\b",
        r"\b(?:classes|catégories|categories)\s*[:=]?\s*\d{1,4}\b",
    ],
    "sample_counts": [
        r"\b\d{2,9}\s*(?:images|samples|échantillons|samples?)\b",
        r"\b(?:training|test|validation)\s*(?:set|dataset)?\s*(?:of|contains|with|=|:)\s*\d{2,9}\b",
    ],
    "metrics": [
        r"\b(?:accuracy|acc|precision|recall|f1|auc|mAP|rank-1|mean\s+average\s+classification\s+accuracy|error\s+rate)\s*(?:of|=|:)\s*[0-9]+(?:\.[0-9]+)?\s*%?\b",
        r"\b[0-9]+(?:\.[0-9]+)?\s*%\s*(?:accuracy|improvement|error|reduction|acc|precision|recall|f1)\b",
    ],
    "hardware": [
        r"\b(?:CPU|GPU|CUDA|NVIDIA|RTX|Intel|AMD)\b.{0,120}",
        r"\b\d+\s*GB\b.{0,80}",
    ],
    "runtime": [
        r"\b(?:runtime|training\s+time|inference\s+time|temps\s+d['’]?exécution|latency|latence)\s*[:=]?\s*[0-9]+(?:\.[0-9]+)?\s*(?:ms|s|sec|seconds|minutes|hours|h)\b",
    ],
    "simulation_or_mesh_parameters": [
        r"\b(?:mesh|maillage|grid|resolution|résolution|ray\s*tracing|multibounce|facet|voxel|cell|window|kernel)\b.{0,160}",
        r"\b(?:order|ordre|size|taille|threshold|seuil|parameter|paramètre|parametre)\s*[:=]?\s*[0-9]+(?:\.[0-9]+)?\b",
    ],
}


def build_technical_detail_profile(card: Dict[str, Any], technical: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extrait les détails techniques explicites : architecture, hyperparamètres,
    protocole d'entraînement, données, métriques, hardware et paramètres numériques.

    Important : aucune valeur n'est inventée. Si les époques, couches ou paramètres
    ne sont pas présents dans les Article Cards/fulltext, le champ reste vide et
    apparaît dans missing_details.
    """
    source_text = build_technical_detail_source_text(card, technical)

    hyperparameters = {
        "epochs": extract_values_by_patterns(source_text, TECHNICAL_DETAIL_PATTERNS["epochs"]),
        "batch_size": extract_values_by_patterns(source_text, TECHNICAL_DETAIL_PATTERNS["batch_size"]),
        "learning_rate": extract_values_by_patterns(source_text, TECHNICAL_DETAIL_PATTERNS["learning_rate"]),
        "optimizer": extract_values_by_patterns(source_text, TECHNICAL_DETAIL_PATTERNS["optimizer"]),
        "loss_function": extract_values_by_patterns(source_text, TECHNICAL_DETAIL_PATTERNS["loss_function"]),
        "dropout": extract_values_by_patterns(source_text, TECHNICAL_DETAIL_PATTERNS["dropout"]),
        "weight_decay": extract_values_by_patterns(source_text, TECHNICAL_DETAIL_PATTERNS["weight_decay"]),
        "momentum": extract_values_by_patterns(source_text, TECHNICAL_DETAIL_PATTERNS["momentum"]),
    }

    architecture = {
        "layers": extract_values_by_patterns(source_text, TECHNICAL_DETAIL_PATTERNS["layers"]),
        "input_shape": extract_values_by_patterns(source_text, TECHNICAL_DETAIL_PATTERNS["input_shape"]),
        "patch_size": extract_values_by_patterns(source_text, TECHNICAL_DETAIL_PATTERNS["patch_size"]),
        "architecture_snippets": extract_snippets_by_patterns(
            source_text,
            [
                r"\b(?:architecture|network|model|backbone|encoder|decoder|MLP|CNN|transformer|vision\s+transformer|module)\b.{0,260}",
                r".{0,120}\b(?:layers?|patches|input\s+image|feature\s+maps?)\b.{0,160}",
            ],
            max_items=5,
        ),
    }

    data_and_protocol = {
        "classes": extract_values_by_patterns(source_text, TECHNICAL_DETAIL_PATTERNS["classes"]),
        "sample_counts": extract_values_by_patterns(source_text, TECHNICAL_DETAIL_PATTERNS["sample_counts"]),
        "dataset_snippets": extract_snippets_by_patterns(
            source_text,
            [
                r"\b(?:dataset|data\s*set|training\s+set|test\s+set|validation\s+set|benchmark)\b.{0,260}",
                r".{0,100}\b(?:classes|images|samples|training|test|validation)\b.{0,180}",
            ],
            max_items=6,
        ),
        "validation_snippets": extract_snippets_by_patterns(
            source_text,
            [
                r"\b(?:validation|evaluation|experiment|baseline|ablation|compare|comparison|metric)\b.{0,280}",
                r".{0,100}\b(?:accuracy|error|precision|recall|F1|AUC|Rank-1|mAP)\b.{0,180}",
            ],
            max_items=6,
        ),
    }

    evaluation_metrics = {
        "metrics": extract_values_by_patterns(source_text, TECHNICAL_DETAIL_PATTERNS["metrics"]),
        "results_snippets": extract_snippets_by_patterns(
            source_text,
            [
                r".{0,100}\b(?:results?|performance|accuracy|error|improvement|reduction|outperform|baseline)\b.{0,220}",
            ],
            max_items=6,
        ),
    }

    implementation = {
        "hardware": extract_values_by_patterns(source_text, TECHNICAL_DETAIL_PATTERNS["hardware"]),
        "runtime": extract_values_by_patterns(source_text, TECHNICAL_DETAIL_PATTERNS["runtime"]),
        "simulation_or_mesh_parameters": extract_values_by_patterns(source_text, TECHNICAL_DETAIL_PATTERNS["simulation_or_mesh_parameters"]),
        "implementation_snippets": extract_snippets_by_patterns(
            source_text,
            [
                r"\b(?:CPU|GPU|CUDA|memory|implementation|runtime|latency|parallel|thread|batch|mesh|grid|simulation|ray\s*tracing)\b.{0,260}",
            ],
            max_items=6,
        ),
    }

    missing_details = []
    if not any(hyperparameters.values()):
        missing_details.append("hyperparamètres d'entraînement non trouvés explicitement")
    if not any(architecture.values()):
        missing_details.append("architecture/couches/input shape non trouvés explicitement")
    if not (data_and_protocol.get("dataset_snippets") or data_and_protocol.get("sample_counts") or data_and_protocol.get("classes")):
        missing_details.append("détails dataset/split/classes non trouvés explicitement")
    if not (evaluation_metrics.get("metrics") or evaluation_metrics.get("results_snippets")):
        missing_details.append("métriques/résultats quantitatifs non trouvés explicitement")
    if not any(implementation.values()):
        missing_details.append("paramètres d'implémentation/hardware/maillage non trouvés explicitement")

    has_any_detail = any([
        any(hyperparameters.values()),
        any(architecture.values()),
        bool(data_and_protocol.get("dataset_snippets") or data_and_protocol.get("sample_counts") or data_and_protocol.get("classes")),
        bool(evaluation_metrics.get("metrics") or evaluation_metrics.get("results_snippets")),
        any(implementation.values()),
    ])

    detail_score = 0
    for block in [hyperparameters, architecture, data_and_protocol, evaluation_metrics, implementation]:
        for value in block.values():
            if isinstance(value, list) and value:
                detail_score += min(3, len(value))
    detail_score = min(100, detail_score * 5)

    return {
        "profile_type": "phase_4_5_v1_9_explicit_technical_details",
        "has_any_detail": bool(has_any_detail),
        "detail_score": detail_score,
        "architecture": architecture,
        "training_hyperparameters": hyperparameters,
        "data_and_protocol": data_and_protocol,
        "evaluation_metrics": evaluation_metrics,
        "implementation_parameters": implementation,
        "missing_details": missing_details,
        "evidence_language": "source_original_may_be_fr_or_en",
        "no_value_invented": True,
        "phase_5_instruction": (
            "Utiliser ces détails techniques seulement s'ils sont présents. "
            "Si les couches, époques, learning rate, batch size ou paramètres ne sont pas renseignés, "
            "écrire que l'article ne donne pas ces informations dans les extraits disponibles, "
            "au lieu d'inventer des valeurs."
        ),
    }


def summarize_technical_detail_profile(profile: Dict[str, Any], *, max_chars: int = 900) -> str:
    if not isinstance(profile, dict) or not profile.get("has_any_detail"):
        return ""
    parts: List[str] = []
    hp = profile.get("training_hyperparameters") or {}
    arch = profile.get("architecture") or {}
    data = profile.get("data_and_protocol") or {}
    metrics = profile.get("evaluation_metrics") or {}
    impl = profile.get("implementation_parameters") or {}

    for label, block, keys in [
        ("architecture", arch, ["layers", "input_shape", "patch_size"]),
        ("hyperparamètres", hp, ["epochs", "batch_size", "learning_rate", "optimizer", "loss_function", "dropout"]),
        ("données/protocole", data, ["classes", "sample_counts"]),
        ("métriques", metrics, ["metrics"]),
        ("implémentation", impl, ["hardware", "runtime", "simulation_or_mesh_parameters"]),
    ]:
        vals: List[str] = []
        for key in keys:
            vals.extend([clean_text(x) for x in as_list(block.get(key)) if clean_text(x)])
        if vals:
            parts.append(f"{label}: " + "; ".join(unique_clean_list(vals)[:5]))

    return truncate(" | ".join(parts), max_chars)


def build_technical_detail_matrix(technical_methods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    matrix: List[Dict[str, Any]] = []
    for item in technical_methods:
        profile = item.get("technical_detail_profile") or {}
        matrix.append({
            "citation_label": item.get("citation_label"),
            "method_or_concept": item.get("concept_label") or item.get("method_name") or item.get("technical_family"),
            "priority_tier": item.get("priority_tier"),
            "usage_type": item.get("usage_type"),
            "detail_score": profile.get("detail_score", 0),
            "has_any_detail": bool(profile.get("has_any_detail")),
            "architecture": profile.get("architecture") or {},
            "training_hyperparameters": profile.get("training_hyperparameters") or {},
            "data_and_protocol": profile.get("data_and_protocol") or {},
            "evaluation_metrics": profile.get("evaluation_metrics") or {},
            "implementation_parameters": profile.get("implementation_parameters") or {},
            "missing_details": profile.get("missing_details") or [],
            "phase_5_instruction": profile.get("phase_5_instruction"),
        })
    return matrix


def is_placeholder_text(value: Any) -> bool:
    s = normalize_for_match(value)
    if not s:
        return True
    return any(marker in s for marker in PLACEHOLDER_MARKERS)


def is_weak_generic_impact(value: Any) -> bool:
    s = normalize_for_match(value)
    if not s:
        return True
    return any(marker in s for marker in WEAK_GENERIC_IMPACT_MARKERS)


def has_substantive_text(value: Any, *, min_tokens: int = 6, min_chars: int = 45) -> bool:
    s = clean_text(value)
    if not s or is_placeholder_text(s):
        return False
    tokens = re.findall(r"[A-Za-zÀ-ÿ0-9_+\-]{3,}", s)
    return len(tokens) >= min_tokens and len(s) >= min_chars


def unique_clean_list(values: List[Any]) -> List[str]:
    out = []
    seen = set()

    for value in values or []:
        s = clean_text(value).strip("[]")
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)

    return out


def format_citation_list(citations: List[str]) -> str:
    clean = unique_clean_list(citations)
    return ", ".join(f"[{c}]" for c in clean)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def tokenize_for_overlap(text: Any) -> set:
    s = normalize_for_match(text)
    tokens = set(re.findall(r"[a-z0-9_]{3,}", s))

    stop = {
        "les", "des", "une", "dans", "pour", "avec", "sur", "aux", "par", "est",
        "the", "and", "for", "with", "from", "that", "this", "are", "was", "were",
        "method", "methods", "article", "paper", "approach", "study",
    }

    return {t for t in tokens if t not in stop}


# ============================================================
# Paths
# ============================================================

def state_of_art_payload_dir(organisme: str, project: str, year: str) -> Path:
    return (
        ROOT_DIR
        / "storage"
        / "organismes"
        / fs_slug(organisme)
        / "projects"
        / fs_slug(project)
        / "years"
        / str(year)
        / "ennoscholar"
        / "state_of_art_payload"
    )


def default_article_cards_payload_path(organisme: str, project: str, year: str) -> Path:
    return state_of_art_payload_dir(organisme, project, year) / "article_cards" / "article_cards_payload.json"


def default_gap_payload_path(organisme: str, project: str, year: str) -> Path:
    return (
        state_of_art_payload_dir(organisme, project, year)
        / "phase_4_scientific_gap"
        / "gap_scientific_payload.json"
    )


def default_argumentation_payload_path(organisme: str, project: str, year: str) -> Path:
    return (
        state_of_art_payload_dir(organisme, project, year)
        / "phase_3_style_memory"
        / "argumentation_profile_payload.json"
    )


def phase_4_5_output_dir(organisme: str, project: str, year: str) -> Path:
    return (
        state_of_art_payload_dir(organisme, project, year)
        / "phase_4_5_scientific_reasoning"
    )


def scientific_reasoning_output_path(organisme: str, project: str, year: str) -> Path:
    return phase_4_5_output_dir(organisme, project, year) / "scientific_reasoning_payload.json"


# ============================================================
# Article Cards extraction
# ============================================================

def find_article_cards_container(payload: Dict[str, Any]) -> List[Any]:
    if not isinstance(payload, dict):
        return []

    for key in [
        "article_cards",
        "cards",
        "articles",
        "selected_article_cards",
        "article_cards_payload",
    ]:
        value = payload.get(key)

        if isinstance(value, list):
            return value

        if isinstance(value, dict):
            return list(value.values())

    for key in [
        "verrou_cards",
        "cards_by_verrou",
        "article_cards_by_verrou",
        "verrous",
    ]:
        value = payload.get(key)

        if isinstance(value, dict):
            merged = []

            for verrou_id, cards in value.items():
                for card in as_list(cards):
                    if isinstance(card, dict):
                        card = dict(card)
                        card.setdefault("verrou_id", verrou_id)
                        merged.append(card)

            if merged:
                return merged

    return []


def extract_quality_status(card: Dict[str, Any]) -> str:
    q = card.get("quality_guard") or {}
    if isinstance(q, dict):
        return clean_text(q.get("status")) or "unknown"
    return "unknown"


def extract_generation_mode(card: Dict[str, Any]) -> str:
    evidence = card.get("evidence") or {}
    if isinstance(evidence, dict):
        return clean_text(evidence.get("generation_mode")) or "unknown"
    return "unknown"


def extract_fulltext_available(card: Dict[str, Any]) -> bool:
    evidence = card.get("evidence") or {}
    if isinstance(evidence, dict):
        return bool(evidence.get("full_text_available"))
    return False


def extract_text_chars(card: Dict[str, Any]) -> int:
    evidence = card.get("evidence") or {}
    if isinstance(evidence, dict):
        return safe_int(evidence.get("text_chars"), 0)
    return 0




# ============================================================
# Phase 2 V3.2 — Evidence Bank helpers
# ============================================================

EVIDENCE_BUCKET_LABELS = {
    "problem": "problème scientifique",
    "solution": "solution / contribution",
    "method": "méthode / principe technique",
    "workflow": "enchaînement technique / étapes",
    "dataset": "données / datasets",
    "validation": "protocole de validation",
    "results": "résultats",
    "limitations": "limites",
    "future_work": "travaux futurs",
    "definition": "définitions / concepts",
}

EVIDENCE_BUCKET_PRIORITY = [
    "problem",
    "solution",
    "method",
    "workflow",
    "dataset",
    "validation",
    "results",
    "limitations",
    "definition",
    "future_work",
]


def get_article_evidence_bank(card: Dict[str, Any]) -> Dict[str, Any]:
    bank = card.get("article_evidence_bank")
    if isinstance(bank, dict):
        return bank

    # Si la Phase 4 a déjà compacté les preuves.
    extracts = card.get("evidence_extracts_for_phase_4_5")
    if isinstance(extracts, dict) and extracts.get("evidence_bank_available"):
        return {
            "version": "from_phase_4_evidence_extracts",
            "paragraph_buckets": {
                bucket: extracts.get(bucket) or []
                for bucket in EVIDENCE_BUCKET_PRIORITY
            },
        }

    return {}


def get_paragraph_buckets(card: Dict[str, Any]) -> Dict[str, Any]:
    bank = get_article_evidence_bank(card)
    buckets = bank.get("paragraph_buckets")
    if isinstance(buckets, dict):
        return buckets
    return {}


def extract_bucket_items(
    card: Dict[str, Any],
    bucket: str,
    max_items: int = 4,
    max_chars: int = 900,
) -> List[Dict[str, Any]]:
    buckets = get_paragraph_buckets(card)
    raw_items = as_list(buckets.get(bucket))

    out = []
    seen = set()

    for item in raw_items:
        if isinstance(item, dict):
            text = clean_text(
                item.get("text")
                or item.get("paragraph")
                or item.get("value")
            )
            section = clean_text(item.get("section"))
            score = safe_float(item.get("score"), 0.0)
            source = clean_text(item.get("source"))
            paragraph_index = item.get("paragraph_index")
            matched_terms = item.get("matched_terms") or []
        else:
            text = clean_text(item)
            section = ""
            score = 0.0
            source = ""
            paragraph_index = None
            matched_terms = []

        if not text:
            continue

        if len(text) < 45 and len(text.split()) < 8:
            continue

        key = normalize_for_match(text)[:220]
        if key in seen:
            continue
        seen.add(key)

        out.append({
            "bucket": bucket,
            "bucket_label": EVIDENCE_BUCKET_LABELS.get(bucket, bucket),
            "section": section,
            "paragraph_index": paragraph_index,
            "text": truncate(text, max_chars),
            "score": score,
            "source": source,
            "matched_terms": matched_terms,
        })

        if len(out) >= max_items:
            break

    return out


def extract_bucket_texts(
    card: Dict[str, Any],
    buckets: List[str],
    max_items_per_bucket: int = 2,
    max_chars: int = 750,
) -> List[str]:
    texts = []
    seen = set()

    for bucket in buckets:
        for item in extract_bucket_items(
            card,
            bucket,
            max_items=max_items_per_bucket,
            max_chars=max_chars,
        ):
            txt = clean_text(item.get("text"))
            if not txt:
                continue
            key = normalize_for_match(txt)[:180]
            if key in seen:
                continue
            seen.add(key)
            texts.append(txt)

    return texts


def first_bucket_text(
    card: Dict[str, Any],
    buckets: List[str],
    fallback: Any = "",
    max_chars: int = 900,
) -> str:
    texts = extract_bucket_texts(
        card,
        buckets,
        max_items_per_bucket=2,
        max_chars=max_chars,
    )
    if texts:
        return truncate(" ".join(texts), max_chars)

    return truncate(fallback, max_chars)


def evidence_bucket_counts(card: Dict[str, Any]) -> Dict[str, int]:
    buckets = get_paragraph_buckets(card)
    return {
        bucket: len(as_list(buckets.get(bucket)))
        for bucket in EVIDENCE_BUCKET_PRIORITY
    }


def has_extractable_evidence_bank(card: Dict[str, Any]) -> bool:
    return any(v > 0 for v in evidence_bucket_counts(card).values())


def build_phase2_evidence_for_reasoning(
    card: Dict[str, Any],
    max_total_chars: int = 6500,
) -> str:
    lines = []

    for bucket in EVIDENCE_BUCKET_PRIORITY:
        items = extract_bucket_items(card, bucket, max_items=2, max_chars=720)
        if not items:
            continue

        label = EVIDENCE_BUCKET_LABELS.get(bucket, bucket)
        lines.append(f"\n[{bucket.upper()} — {label}]")

        for item in items:
            section = clean_text(item.get("section"))
            section_txt = f" ({section})" if section else ""
            lines.append(f"-{section_txt} {item.get('text')}")

    text = "\n".join(lines).strip()

    if not text:
        text = clean_text(card.get("phase2_evidence_for_reasoning"))

    return truncate(text, max_total_chars)


def build_evidence_extracts_for_reasoning(card: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "evidence_bank_available": has_extractable_evidence_bank(card),
        "bucket_counts": evidence_bucket_counts(card),
        "problem": extract_bucket_items(card, "problem", max_items=3, max_chars=750),
        "solution": extract_bucket_items(card, "solution", max_items=3, max_chars=750),
        "method": extract_bucket_items(card, "method", max_items=3, max_chars=800),
        "workflow": extract_bucket_items(card, "workflow", max_items=3, max_chars=800),
        "dataset": extract_bucket_items(card, "dataset", max_items=3, max_chars=750),
        "validation": extract_bucket_items(card, "validation", max_items=3, max_chars=750),
        "results": extract_bucket_items(card, "results", max_items=3, max_chars=750),
        "limitations": extract_bucket_items(card, "limitations", max_items=3, max_chars=750),
        "future_work": extract_bucket_items(card, "future_work", max_items=2, max_chars=650),
        "definition": extract_bucket_items(card, "definition", max_items=3, max_chars=750),
        "phase2_evidence_for_reasoning": build_phase2_evidence_for_reasoning(card),
    }


def extract_old_technical_block_fallback(card: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fallback uniquement : utilisé quand article_evidence_bank est absent.
    """
    tcl = card.get("technical_concept_limits")
    if isinstance(tcl, dict) and tcl:
        return {
            "method_name": clean_text(tcl.get("method_name")),
            "technical_family": clean_text(tcl.get("technical_family")),
            "principle": clean_text(tcl.get("principle") or tcl.get("technical_principle")),
            "mechanism": clean_text(tcl.get("mechanism")),
            "concept_limits": [clean_text(x) for x in as_list(tcl.get("concept_limits")) if clean_text(x)],
            "transposability_limits": [clean_text(x) for x in as_list(tcl.get("transposability_limits")) if clean_text(x)],
            "impact_on_verrou": clean_text(tcl.get("impact_on_verrou")),
            "remaining_uncertainty": clean_text(tcl.get("remaining_uncertainty")),
            "cir_exploitation": clean_text(tcl.get("cir_exploitation")),
            "source": clean_text(tcl.get("source")) or "technical_concept_limits",
        }

    tma = card.get("technical_method_analysis")
    if isinstance(tma, dict) and tma:
        return {
            "method_name": clean_text(tma.get("method_name")),
            "technical_family": clean_text(tma.get("technical_family")),
            "principle": clean_text(tma.get("technical_principle") or tma.get("principle")),
            "mechanism": clean_text(tma.get("mechanism")),
            "concept_limits": [clean_text(x) for x in as_list(tma.get("concept_limits")) if clean_text(x)],
            "transposability_limits": [clean_text(x) for x in as_list(tma.get("transposability_limits")) if clean_text(x)],
            "impact_on_verrou": clean_text(tma.get("impact_on_verrou")),
            "remaining_uncertainty": clean_text(tma.get("remaining_uncertainty")),
            "cir_exploitation": clean_text(tma.get("cir_exploitation")),
            "source": clean_text(tma.get("source")) or "technical_method_analysis",
        }

    return {
        "method_name": clean_text(card.get("method_name")),
        "technical_family": clean_text(card.get("technical_family")),
        "principle": clean_text(card.get("technical_principle")),
        "mechanism": clean_text(card.get("mechanism")),
        "concept_limits": [clean_text(x) for x in as_list(card.get("concept_limits")) if clean_text(x)],
        "transposability_limits": [clean_text(x) for x in as_list(card.get("transposability_limits")) if clean_text(x)],
        "impact_on_verrou": clean_text(card.get("impact_on_verrou")),
        "remaining_uncertainty": clean_text(card.get("remaining_uncertainty")),
        "cir_exploitation": clean_text(card.get("cir_exploitation")),
        "source": "flat_fields",
    }


def build_extractive_technical_block(card: Dict[str, Any]) -> Dict[str, Any]:
    """
    Bloc central Phase 4.5 :
    transforme les paragraphes originaux Phase 2 en matière de raisonnement,
    sans générer une rédaction finale.
    """
    fallback = extract_old_technical_block_fallback(card)

    title = clean_text(card.get("title") or card.get("article_title") or card.get("paper_title"))

    method_name = clean_text(
        card.get("method_name")
        or fallback.get("method_name")
    )

    technical_family = clean_text(
        card.get("technical_family")
        or fallback.get("technical_family")
    )

    principle = first_bucket_text(
        card,
        ["method", "solution", "definition"],
        fallback=fallback.get("principle") or card.get("method"),
        max_chars=1100,
    )

    mechanism = first_bucket_text(
        card,
        ["workflow", "method"],
        fallback=fallback.get("mechanism") or card.get("method"),
        max_chars=1200,
    )

    dataset_context = first_bucket_text(
        card,
        ["dataset"],
        fallback="",
        max_chars=850,
    )

    validation_protocol = first_bucket_text(
        card,
        ["validation", "dataset"],
        fallback="",
        max_chars=900,
    )

    reported_results = first_bucket_text(
        card,
        ["results"],
        fallback=card.get("results"),
        max_chars=850,
    )

    problem_context = first_bucket_text(
        card,
        ["problem"],
        fallback=card.get("abstract"),
        max_chars=850,
    )

    solution_context = first_bucket_text(
        card,
        ["solution"],
        fallback="",
        max_chars=850,
    )

    limits = extract_bucket_texts(
        card,
        ["limitations", "future_work"],
        max_items_per_bucket=3,
        max_chars=420,
    )

    if not limits:
        limits = [clean_text(x) for x in as_list(fallback.get("concept_limits")) if clean_text(x)]

    trans_limits = []
    trans_limits.extend(extract_bucket_texts(card, ["validation", "dataset"], max_items_per_bucket=2, max_chars=420))
    trans_limits.extend(extract_bucket_texts(card, ["limitations"], max_items_per_bucket=2, max_chars=420))

    if not trans_limits:
        trans_limits = [clean_text(x) for x in as_list(fallback.get("transposability_limits")) if clean_text(x)]

    impact = first_bucket_text(
        card,
        ["problem", "validation", "limitations"],
        fallback=fallback.get("impact_on_verrou") or card.get("relevance"),
        max_chars=800,
    )

    remaining_uncertainty = first_bucket_text(
        card,
        ["limitations", "validation", "dataset"],
        fallback=fallback.get("remaining_uncertainty"),
        max_chars=750,
    )

    phase2_context = build_phase2_evidence_for_reasoning(card)

    if not technical_family:
        # Famille non métier, uniquement à partir des signaux textuels.
        family_text = normalize_for_match(" ".join([title, principle, mechanism, dataset_context, validation_protocol]))
        if any(k in family_text for k in ["augmentation", "synthetic", "synth", "generate", "generation", "mask"]):
            technical_family = "augmentation de données et génération de variantes"
        elif any(k in family_text for k in ["uncertainty", "quantification", "probabilistic", "bayesian"]):
            technical_family = "quantification d'incertitude et fiabilité"
        elif any(k in family_text for k in ["classification", "classifier", "cnn", "neural", "deep learning"]):
            technical_family = "classification par apprentissage automatique"
        elif any(k in family_text for k in ["validation", "evaluation", "benchmark", "metric"]):
            technical_family = "validation expérimentale et évaluation"
        else:
            technical_family = ""

    return {
        "method_name": method_name,
        "technical_family": technical_family,
        "principle": principle,
        "mechanism": mechanism,
        "dataset_context": dataset_context,
        "validation_protocol": validation_protocol,
        "reported_results": reported_results,
        "problem_context": problem_context,
        "solution_context": solution_context,
        "concept_limits": clean_evidence_items(limits, max_items=5, max_chars=450),
        "transposability_limits": clean_evidence_items(trans_limits, max_items=5, max_chars=450),
        "impact_on_verrou": impact,
        "remaining_uncertainty": remaining_uncertainty,
        "cir_exploitation": (
            "À utiliser comme matière scientifique pour expliquer le principe, le mécanisme, "
            "les conditions de validation et les limites de transposition. Ne pas recopier les paragraphes bruts tels quels."
        ),
        "phase2_evidence_for_reasoning": phase2_context,
        "evidence_extracts": build_evidence_extracts_for_reasoning(card),
        "source": "article_evidence_bank_v3_2_extractive_first",
    }

def extract_technical_block(card: Dict[str, Any]) -> Dict[str, Any]:
    """
    V1.8 :
    Priorité absolue au bloc extractif Phase 2 V3.2 :
        article_evidence_bank.paragraph_buckets

    Les anciens champs technical_method_analysis / technical_concept_limits
    restent seulement des fallbacks de compatibilité.
    """
    if has_extractable_evidence_bank(card):
        return build_extractive_technical_block(card)

    return extract_old_technical_block_fallback(card)


def normalize_article_card(card: Dict[str, Any], index: int) -> Dict[str, Any]:
    citation_label = clean_text(
        card.get("citation_label")
        or card.get("citation_id")
        or card.get("reference_id")
        or card.get("article_ref")
        or card.get("ref")
        or f"A{index}"
    ).strip("[]")

    title = clean_text(
        card.get("title")
        or card.get("article_title")
        or card.get("paper_title")
    )

    authors = card.get("authors") or card.get("author_names") or []
    if isinstance(authors, list):
        authors_text = ", ".join(clean_text(a) for a in authors[:6] if clean_text(a))
    else:
        authors_text = clean_text(authors)

    technical = extract_technical_block(card)
    evidence_extracts = technical.get("evidence_extracts") or build_evidence_extracts_for_reasoning(card)

    abstract = first_bucket_text(
        card,
        ["problem", "solution"],
        fallback=(
            card.get("abstract_for_writer")
            or card.get("abstract_fr")
            or card.get("abstract_original")
            or card.get("abstract")
            or card.get("summary")
            or card.get("resume")
        ),
        max_chars=1000,
    )

    method = first_bucket_text(
        card,
        ["method", "workflow"],
        fallback=(
            card.get("method")
            or card.get("methodology")
            or card.get("methods")
            or card.get("approach")
            or card.get("methode")
            or card.get("méthode")
        ),
        max_chars=1200,
    )

    results = first_bucket_text(
        card,
        ["results", "validation"],
        fallback=(
            card.get("results")
            or card.get("main_results")
            or card.get("resultats")
            or card.get("résultats")
            or card.get("findings")
        ),
        max_chars=1000,
    )

    limitations = first_bucket_text(
        card,
        ["limitations", "future_work"],
        fallback=(
            card.get("limitations")
            or card.get("limites")
            or card.get("limits")
            or card.get("article_limitations")
            or card.get("weaknesses")
        ),
        max_chars=1000,
    )

    relevance = first_bucket_text(
        card,
        ["problem", "validation", "limitations"],
        fallback=(
            card.get("relevance")
            or card.get("pertinence")
            or card.get("why_relevant")
            or card.get("justification")
            or card.get("relevance_reason")
            or card.get("limite_pour_notre_projet")
        ),
        max_chars=1000,
    )

    return {
        "citation_label": citation_label,
        "article_id": clean_text(
            card.get("article_id")
            or card.get("paper_id")
            or card.get("semantic_scholar_id")
            or card.get("openalex_id")
            or card.get("arxiv_id")
            or card.get("id")
            or card.get("doi")
        ),
        "title": title,
        "authors": authors_text,
        "year": clean_text(
            card.get("year")
            or card.get("publication_year")
            or card.get("published_year")
        ),
        "abstract": abstract,
        "method": method,
        "results": results,
        "limitations": limitations,
        "relevance": relevance,
        "tag": clean_text(
            card.get("tag")
            or card.get("semantic_tag")
            or card.get("article_tag")
            or card.get("relation_type")
            or card.get("category")
            or card.get("role")
        ),
        "score": safe_float(card.get("score"), 0.0),
        "quality_status": extract_quality_status(card),
        "generation_mode": extract_generation_mode(card),
        "full_text_available": extract_fulltext_available(card) or has_extractable_evidence_bank(card),
        "text_chars": extract_text_chars(card),
        "article_evidence_bank_available": has_extractable_evidence_bank(card),
        "article_evidence_bucket_counts": evidence_bucket_counts(card),
        "phase2_evidence_for_reasoning": technical.get("phase2_evidence_for_reasoning") or build_phase2_evidence_for_reasoning(card),
        "evidence_extracts_for_reasoning": evidence_extracts,
        "technical_method_analysis": technical,
        "method_name": technical.get("method_name"),
        "technical_family": technical.get("technical_family"),
        "technical_principle": technical.get("principle"),
        "mechanism": technical.get("mechanism"),
        "dataset_context": technical.get("dataset_context"),
        "validation_protocol": technical.get("validation_protocol"),
        "reported_results": technical.get("reported_results"),
        "problem_context": technical.get("problem_context"),
        "solution_context": technical.get("solution_context"),
        "concept_limits": technical.get("concept_limits") or [],
        "transposability_limits": technical.get("transposability_limits") or [],
        "impact_on_verrou": technical.get("impact_on_verrou"),
        "remaining_uncertainty": technical.get("remaining_uncertainty"),
        "cir_exploitation": technical.get("cir_exploitation"),
        "raw": card,
    }


def extract_article_cards(article_cards_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_cards = find_article_cards_container(article_cards_payload)
    cards = []

    for i, card in enumerate(raw_cards, 1):
        if not isinstance(card, dict):
            continue

        normalized = normalize_article_card(card, i)

        if normalized["citation_label"] and normalized["title"]:
            cards.append(normalized)

    return cards


def article_cards_by_citation(article_cards: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {
        clean_text(card.get("citation_label")).strip("[]"): card
        for card in article_cards
        if clean_text(card.get("citation_label"))
    }


# ============================================================
# Scientific family inference
# ============================================================

GENERIC_FAMILY_RULES = [
    {
        "family_id": "augmentation_generation_variants",
        "label": "augmentation de données et génération de variantes",
        "keywords": [
            "augmentation", "augment", "synthetic", "synthétique", "synthetique",
            "mask", "masque", "adversarial", "perturbation", "transformation",
            "variant", "occlusion", "generation", "génération", "generer", "générer",
        ],
    },
    {
        "family_id": "sparse_modeling_signal_representation",
        "label": "modélisation sparse et représentation du signal",
        "keywords": [
            "sparse", "sparsity", "sparsité", "signal", "phase history",
            "model-based", "model based", "representation", "représentation",
            "scattering", "scatter", "centre de diffusion", "diffusion",
        ],
    },
    {
        "family_id": "feature_selection_dimensionality",
        "label": "sélection de caractéristiques et réduction de dimension",
        "keywords": [
            "dimensionality", "dimensionnalité", "dimensionnalite", "feature",
            "caractéristique", "caracteristique", "mutual information",
            "information mutuelle", "mrmr", "selection", "sélection",
            "redundancy", "redondance",
        ],
    },
    {
        "family_id": "deep_learning_classification",
        "label": "classification par apprentissage profond",
        "keywords": [
            "cnn", "convolutional", "deep learning", "apprentissage profond",
            "resnet", "efficientnet", "classification", "classifier", "classifieur",
            "neural network", "réseau de neurones",
        ],
    },
    {
        "family_id": "evaluation_robustness_generalization",
        "label": "évaluation, robustesse et généralisation",
        "keywords": [
            "evaluation", "évaluation", "robust", "robustesse", "generalization",
            "généralisation", "generalisation", "benchmark", "performance",
            "accuracy", "precision", "précision", "validation",
        ],
    },
    {
        "family_id": "image_processing_segmentation",
        "label": "traitement d’image et segmentation",
        "keywords": [
            "image processing", "traitement d'image", "segmentation", "contour",
            "thresholding", "seuillage", "vision", "computer vision", "u-net", "unet",
        ],
    },
]


def text_for_family_detection(card: Dict[str, Any]) -> str:
    return normalize_for_match(
        " ".join([
            card.get("title") or "",
            card.get("abstract") or "",
            card.get("method") or "",
            card.get("results") or "",
            card.get("limitations") or "",
            card.get("relevance") or "",
            card.get("technical_family") or "",
            card.get("technical_principle") or "",
            card.get("mechanism") or "",
            card.get("dataset_context") or "",
            card.get("validation_protocol") or "",
            card.get("reported_results") or "",
            card.get("problem_context") or "",
            card.get("solution_context") or "",
            card.get("phase2_evidence_for_reasoning") or "",
            " ".join(card.get("concept_limits") or []),
            " ".join(card.get("transposability_limits") or []),
        ])
    )


def infer_article_family(card: Dict[str, Any]) -> Dict[str, Any]:
    text = text_for_family_detection(card)

    best_rule = None
    best_hits = []

    for rule in GENERIC_FAMILY_RULES:
        hits = []
        for kw in rule["keywords"]:
            if normalize_for_match(kw) in text:
                hits.append(kw)

        if best_rule is None or len(hits) > len(best_hits):
            best_rule = rule
            best_hits = hits

    if best_rule and best_hits:
        return {
            "family_id": best_rule["family_id"],
            "family_label": best_rule["label"],
            "match_keywords": best_hits[:10],
            "confidence": round(min(1.0, 0.35 + len(best_hits) * 0.10), 2),
        }

    return {
        "family_id": "methodological_context",
        "family_label": "contexte méthodologique général",
        "match_keywords": [],
        "confidence": 0.35,
    }


def compute_family_evidence_strength(family: Dict[str, Any]) -> str:
    direct = family.get("direct_citations") or []
    related = family.get("related_citations") or []
    methodological = family.get("methodological_citations") or []
    background = family.get("background_citations") or []

    if direct and related:
        return "mixed_direct_related"

    if direct:
        return "direct"

    if related:
        return "related"

    if methodological:
        return "methodological"

    if background:
        return "background"

    return "context"


def normalize_usage_type(value: Any) -> str:
    """
    Normalise les types d'usage venant de Phase 4 et Phase 4.5.

    Correction ciblée :
    Phase 4 produit notamment :
      - direct_evidence
      - related_evidence
      - methodological_context
      - background_context
      - weak_context

    Phase 4.5 utilise plutôt :
      - direct_evidence
      - related_evidence
      - methodological_evidence
      - background_evidence

    Cette fonction mappe explicitement les noms Phase 4 pour éviter de déclasser
    les articles fondamentaux / méthodologiques en contexte faible ou inconnu.
    """
    usage = normalize_for_match(value)

    if usage in {
        "direct_evidence",
        "direct",
        "preuve_directe",
        "preuve directe",
        "article_direct",
        "articles_directs",
        "direct_article",
        "direct_articles",
    }:
        return "direct_evidence"

    if usage in {
        "related_evidence",
        "related",
        "connexe",
        "connexes",
        "preuve_connexe",
        "preuve connexe",
        "article_connexe",
        "articles_connexes",
        "related_article",
        "related_articles",
    }:
        return "related_evidence"

    if usage in {
        "methodological_evidence",
        "methodological_context",
        "methodological",
        "methodologie",
        "méthodologie",
        "methodologique",
        "méthodologique",
        "methodological support",
        "contexte_methodologique",
        "contexte méthodologique",
    }:
        return "methodological_evidence"

    if usage in {
        "background_evidence",
        "background_context",
        "weak_context",
        "background",
        "context",
        "contexte",
        "fondamental",
        "fondamentaux",
        "fundamental",
        "fundamentals",
        "article_fondamental",
        "articles_fondamentaux",
        "background_article",
        "background_articles",
    }:
        return "background_evidence"

    return usage or "context_evidence"

def compute_textual_proximity_score(
    *,
    verrou_gap: Dict[str, Any],
    card: Dict[str, Any],
) -> float:
    """
    Score générique de proximité au verrou.
    V1.8 : inclut les paragraphes extractifs Phase 2 V3.2.
    """

    verrou_text = " ".join([
        clean_text(verrou_gap.get("verrou_title")),
        clean_text(verrou_gap.get("objectif_rd")),
        clean_text(verrou_gap.get("scientific_gap")),
        clean_text(verrou_gap.get("rd_justification")),
        " ".join(clean_text(x) for x in as_list(verrou_gap.get("non_transposability"))),
        " ".join(clean_text(x) for x in as_list(verrou_gap.get("article_limitations"))),
    ])

    card_text = " ".join([
        clean_text(card.get("title")),
        clean_text(card.get("abstract")),
        clean_text(card.get("method")),
        clean_text(card.get("results")),
        clean_text(card.get("limitations")),
        clean_text(card.get("relevance")),
        clean_text(card.get("technical_family")),
        clean_text(card.get("technical_principle")),
        clean_text(card.get("mechanism")),
        clean_text(card.get("dataset_context")),
        clean_text(card.get("validation_protocol")),
        clean_text(card.get("reported_results")),
        clean_text(card.get("problem_context")),
        clean_text(card.get("solution_context")),
        clean_text(card.get("phase2_evidence_for_reasoning")),
        " ".join(card.get("concept_limits") or []),
        " ".join(card.get("transposability_limits") or []),
        clean_text(card.get("impact_on_verrou")),
        clean_text(card.get("cir_exploitation")),
    ])

    vt = tokenize_for_overlap(verrou_text)
    ct = tokenize_for_overlap(card_text)

    if not vt or not ct:
        return 0.0

    overlap = len(vt & ct)
    ratio = overlap / max(1, min(len(vt), len(ct)))

    return round(min(1.0, ratio), 3)


def compute_article_weight(
    *,
    verrou_gap: Dict[str, Any],
    card: Dict[str, Any],
    usage_type: str,
    family_confidence: float,
) -> Dict[str, Any]:
    """
    Pondération générique V1.4.
    Aucun article n'est ignoré, mais les Article Cards faibles ne deviennent plus "core".
    """
    usage_type = normalize_usage_type(usage_type)

    score = 0.0
    factors = []

    if usage_type == "direct_evidence":
        score += 45
        factors.append("usage_direct:+45")
    elif usage_type == "related_evidence":
        score += 22
        factors.append("usage_connexe:+22")
    elif usage_type == "methodological_evidence":
        score += 18
        factors.append("usage_methodologique:+18")
    elif usage_type == "background_evidence":
        score += 12
        factors.append("usage_background:+12")
    else:
        score += 10
        factors.append("usage_context:+10")

    db_score = safe_float(card.get("score"), 0.0)
    if db_score > 0:
        add = min(12.0, db_score * 12.0)
        score += add
        factors.append(f"article_score:+{round(add, 2)}")

    proximity = compute_textual_proximity_score(verrou_gap=verrou_gap, card=card)
    if proximity > 0:
        add = proximity * 25.0
        score += add
        factors.append(f"textual_proximity:+{round(add, 2)}")

    family_add = min(8.0, safe_float(family_confidence, 0.0) * 8.0)
    score += family_add
    factors.append(f"family_confidence:+{round(family_add, 2)}")

    quality_status = clean_text(card.get("quality_status"))
    if quality_status == "valid":
        score += 12
        factors.append("quality_valid:+12")
    elif quality_status == "valid_with_warnings":
        score += 5
        factors.append("quality_warnings:+5")
    elif quality_status == "invalid":
        score -= 22
        factors.append("quality_invalid:-22")
    else:
        score -= 5
        factors.append("quality_unknown:-5")

    fulltext_available = bool(card.get("full_text_available"))
    if fulltext_available:
        score += 10
        factors.append("fulltext_available:+10")
    else:
        score -= 15
        factors.append("no_fulltext:-15")

    technical = card.get("technical_method_analysis") or {}

    principle = clean_text(
        technical.get("principle")
        or technical.get("technical_principle")
        or card.get("technical_principle")
    )

    mechanism = clean_text(
        technical.get("mechanism")
        or card.get("mechanism")
    )

    method_norm = normalize_method_name_with_fallback(
        raw_method_name=technical.get("method_name") or card.get("method_name"),
        technical_family=technical.get("technical_family") or card.get("technical_family"),
        title=card.get("title"),
    )

    real_concept_limits = clean_evidence_items(card.get("concept_limits") or technical.get("concept_limits") or [])
    real_trans_limits = clean_evidence_items(card.get("transposability_limits") or technical.get("transposability_limits") or [])
    impact = clean_text(card.get("impact_on_verrou") or technical.get("impact_on_verrou"))

    has_real_technical_analysis = bool(
        method_norm.get("method_name_valid")
        or has_substantive_text(principle, min_tokens=7, min_chars=55)
        or has_substantive_text(mechanism, min_tokens=7, min_chars=55)
        or real_concept_limits
        or real_trans_limits
        or (has_substantive_text(impact, min_tokens=6, min_chars=45) and not is_weak_generic_impact(impact))
    )

    weak_method_name = not bool(method_norm.get("method_name_valid"))

    if has_real_technical_analysis:
        score += 10
        factors.append("technical_analysis:+10")
    else:
        score -= 15
        factors.append("weak_or_missing_technical_analysis:-15")

    if weak_method_name:
        score -= 8
        factors.append("weak_method_name:-8")

    if real_concept_limits and has_real_technical_analysis:
        score += 5
        factors.append("real_concept_limits:+5")
    elif card.get("concept_limits"):
        score -= 3
        factors.append("placeholder_or_generic_concept_limits:-3")

    if real_trans_limits and has_real_technical_analysis:
        score += 5
        factors.append("real_transposability_limits:+5")
    elif card.get("transposability_limits"):
        score -= 3
        factors.append("placeholder_or_generic_transposability_limits:-3")

    if impact and not is_weak_generic_impact(impact) and has_real_technical_analysis:
        score += 5
        factors.append("specific_impact_on_verrou:+5")
    elif impact:
        score -= 3
        factors.append("generic_impact_on_verrou:-3")

    # Plafonds génériques : ne rien ignorer, mais éviter les faux "core".
    cap = 100.0

    if usage_type == "related_evidence":
        cap = min(cap, 62.0)
        factors.append("cap_related_max_62")

    if usage_type in {"methodological_evidence", "methodological_context", "context_evidence"}:
        cap = min(cap, 58.0)
        factors.append("cap_methodological_or_context_max_58")

    if usage_type == "background_evidence":
        cap = min(cap, 45.0)
        factors.append("cap_background_max_45")

    if not fulltext_available:
        cap = min(cap, 52.0)
        factors.append("cap_no_fulltext_max_52")

    if quality_status == "invalid":
        cap = min(cap, 45.0)
        factors.append("cap_invalid_max_45")

    if weak_method_name:
        cap = min(cap, 68.0)
        factors.append("cap_weak_method_name_max_68")

    if not has_real_technical_analysis:
        cap = min(cap, 38.0)
        factors.append("cap_weak_technical_max_38")

    score = max(5.0, min(cap, score))

    if score >= 75:
        tier = "core"
    elif score >= 55:
        tier = "important"
    elif score >= 35:
        tier = "support"
    else:
        tier = "context_low_confidence"

    return {
        "score": round(score, 2),
        "tier": tier,
        "factors": factors,
        "textual_proximity": proximity,
        "quality_status": quality_status,
        "full_text_available": fulltext_available,
        "usage_type": usage_type,
        "has_real_technical_analysis": has_real_technical_analysis,
        "method_name_valid": bool(method_norm.get("method_name_valid")),
        "method_name_warning": method_norm.get("extraction_warning"),
        "no_article_ignored": True,
    }


def citation_sort_key(item: Dict[str, Any]) -> tuple:
    w = item.get("weight") or {}
    usage = normalize_usage_type(item.get("usage_type"))
    usage_priority = {
        "direct_evidence": 0,
        "related_evidence": 1,
        "methodological_evidence": 2,
        "background_evidence": 3,
    }.get(usage, 4)

    return (
        usage_priority,
        -safe_float(w.get("score"), 0.0),
        clean_text(item.get("citation_label")),
    )


# ============================================================
# Families
# ============================================================

def create_minimal_card_from_support_item(item: Dict[str, Any]) -> Dict[str, Any]:
    citation = clean_text(item.get("citation_label")).strip("[]")
    return {
        "citation_label": citation,
        "article_id": clean_text(item.get("article_id")),
        "title": clean_text(item.get("title") or item.get("article_title") or citation),
        "authors": "",
        "year": "",
        "abstract": "",
        "method": "",
        "results": "",
        "limitations": "",
        "relevance": clean_text(item.get("relevance") or item.get("selection_reason")),
        "tag": clean_text(item.get("article_usage_label")),
        "score": 0.0,
        "quality_status": "missing_card",
        "generation_mode": "missing_card",
        "full_text_available": False,
        "text_chars": 0,
        "technical_method_analysis": {},
        "method_name": "",
        "technical_family": "",
        "technical_principle": "",
        "mechanism": "",
        "concept_limits": [],
        "transposability_limits": [],
        "impact_on_verrou": "",
        "remaining_uncertainty": "",
        "cir_exploitation": "",
        "raw": item,
    }


def build_families_from_articles(
    verrou_gap: Dict[str, Any],
    article_cards: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    cards_map = article_cards_by_citation(article_cards)
    supporting_articles = verrou_gap.get("supporting_articles") or []

    family_map: Dict[str, Dict[str, Any]] = {}

    for item in supporting_articles:
        if not isinstance(item, dict):
            continue

        citation = clean_text(item.get("citation_label")).strip("[]")
        if not citation:
            continue

        usage_type = normalize_usage_type(item.get("article_usage_type"))
        usage_label = clean_text(item.get("article_usage_label"))

        card = cards_map.get(citation) or create_minimal_card_from_support_item(item)

        fam = infer_article_family(card)
        fid = fam["family_id"]

        if fid not in family_map:
            family_map[fid] = {
                "family_id": fid,
                "family_label": fam["family_label"],

                # Important :
                # all_citations = traçabilité + conservation.
                # Phase 5 lit direct/related séparément + priorité.
                "all_citations": [],
                "direct_citations": [],
                "related_citations": [],
                "methodological_citations": [],
                "background_citations": [],

                "weighted_citations": [],
                "evidence_strength": "context",
                "signals": [],
                "article_titles_for_traceability_only": [],
                "technical_methods": [],
            }

        family = family_map[fid]

        weight = compute_article_weight(
            verrou_gap=verrou_gap,
            card=card,
            usage_type=usage_type,
            family_confidence=fam.get("confidence", 0.35),
        )

        family["all_citations"].append(citation)

        if usage_type == "direct_evidence":
            family["direct_citations"].append(citation)
        elif usage_type == "related_evidence":
            family["related_citations"].append(citation)
        elif usage_type == "methodological_evidence":
            family["methodological_citations"].append(citation)
        elif usage_type == "background_evidence":
            family["background_citations"].append(citation)
        else:
            family["background_citations"].append(citation)

        family["signals"].extend(fam.get("match_keywords") or [])

        family["weighted_citations"].append({
            "citation_label": citation,
            "usage_type": usage_type,
            "usage_label": usage_label,
            "weight": weight,
            "quality_status": card.get("quality_status"),
            "full_text_available": card.get("full_text_available"),
            "technical_analysis_available": bool(card.get("technical_method_analysis")),
            "no_article_ignored": True,
        })

        family["article_titles_for_traceability_only"].append({
            "citation_label": citation,
            "title": card.get("title"),
            "usage_type": usage_type,
            "usage_label": usage_label,
            "family_inference_keywords": fam.get("match_keywords") or [],
            "family_confidence": fam.get("confidence"),
            "weight": weight,
            "traceability_only": True,
        })

        technical = build_single_technical_method_reasoning(
            verrou_gap=verrou_gap,
            card=card,
            citation=citation,
            usage_type=usage_type,
            usage_label=usage_label,
            weight=weight,
        )
        family["technical_methods"].append(technical)

    families = list(family_map.values())

    for family in families:
        family["signals"] = sorted(set(family["signals"]))
        family["all_citations"] = unique_clean_list(family["all_citations"])
        family["direct_citations"] = unique_clean_list(family["direct_citations"])
        family["related_citations"] = unique_clean_list(family["related_citations"])
        family["methodological_citations"] = unique_clean_list(family["methodological_citations"])
        family["background_citations"] = unique_clean_list(family["background_citations"])

        family["weighted_citations"] = sorted(
            family["weighted_citations"],
            key=lambda x: citation_sort_key(x),
        )

        family["technical_methods"] = sorted(
            family["technical_methods"],
            key=lambda x: -safe_float(x.get("priority_score"), 0.0),
        )

        family["evidence_strength"] = compute_family_evidence_strength(family)

        family["phase_5_usage"] = {
            "use_direct_citations_as_core_proof": bool(family["direct_citations"]),
            "use_related_citations_as_support_only": bool(family["related_citations"]),
            "do_not_merge_related_as_direct": True,
            "do_not_ignore_low_confidence_articles": True,
            "citation_instruction": (
                "Utiliser direct_citations pour les preuves centrales. "
                "Utiliser related_citations uniquement comme appui méthodologique ou discussion de transposabilité. "
                "Conserver les citations faibles en contexte si elles ont été sélectionnées, sans les transformer en preuve forte."
            ),
        }

    priority = {
        "direct": 0,
        "mixed_direct_related": 1,
        "related": 2,
        "methodological": 3,
        "background": 4,
        "context": 5,
    }

    families.sort(
        key=lambda x: (
            priority.get(x.get("evidence_strength"), 9),
            -max([safe_float(w.get("weight", {}).get("score"), 0.0) for w in x.get("weighted_citations", [])] or [0.0]),
            -len(x.get("direct_citations") or []),
            -len(x.get("related_citations") or []),
            x.get("family_label") or "",
        )
    )

    return families


# ============================================================
# Technical reasoning
# ============================================================

# ============================================================
# Extraction hardening — validation générique multi-domaine V1.6
# ============================================================

# Important : cette version ne contient aucune liste noire propre à un domaine.
# Le système est multi-domaine : on valide un nom de méthode à partir de sa forme et de son contexte,
# pas à partir d'une liste de termes scientifiques spécifiques.

GENERIC_METHOD_NAMES = {
    "", "unknown", "n/a", "na", "none", "null",
    "method", "methods", "methodology", "approach", "approaches",
    "technique", "techniques", "model", "models", "framework", "frameworks",
    "algorithm", "algorithms", "system", "systems", "pipeline", "pipelines",
    "interaction", "interactions", "exploring", "exploration", "study", "survey", "review",
    "analysis", "classification", "detection", "segmentation", "prediction", "recognition",
    "learning", "machine learning", "deep learning", "neural network", "neural networks",
    "data", "dataset", "datasets", "features", "images", "signals", "results",
    "augmentation", "evaluation", "performance", "robustness",
    "non explicitement nommé", "non explicitement nomme",
    "non explicitement indiqué", "non explicitement indique",
}

METHOD_ROLE_HINTS = {
    "method", "methods", "approach", "approaches", "algorithm", "framework",
    "model", "models", "architecture", "pipeline", "strategy", "scheme",
    "estimator", "classifier", "detector", "generator", "encoder", "decoder",
    "augmentation", "regularization", "optimization", "selection", "representation",
    "méthode", "méthodes", "modele", "modèle", "modèles", "approche", "approches",
}

TITLE_CONNECTORS = {
    "for", "in", "on", "with", "using", "via", "through", "towards", "toward",
    "pour", "dans", "sur", "avec", "par", "via",
}

TITLE_BAD_STARTERS = {
    "a", "an", "the", "une", "un", "le", "la", "les",
    "exploring", "explore", "study", "studying", "survey", "review", "analysis", "towards", "toward",
    "evaluation", "evaluating", "assessment", "assessing", "comparison", "comparing",
}

METHOD_ROLE_WORD_RE = (
    r"method|methods|méthode|méthodes|approach|approaches|approche|approches|"
    r"model|models|modèle|modèles|modele|modeles|framework|frameworks|"
    r"algorithm|algorithms|algorithme|algorithmes|pipeline|strategy|scheme|"
    r"estimator|classifier|detector|generator|encoder|decoder|architecture"
)


def _tokens(value: Any) -> List[str]:
    return re.findall(r"[A-Za-zÀ-ÿ0-9_+\-]{2,}", clean_text(value))


def _norm_tokens(value: Any) -> List[str]:
    return re.findall(r"[a-z0-9_+\-]{2,}", normalize_for_match(value))


def _is_bad_title_phrase(value: Any) -> bool:
    toks = _norm_tokens(value)
    if not toks:
        return True
    if toks[0] in TITLE_BAD_STARTERS:
        return True
    # Un long morceau de titre narratif n'est généralement pas un nom de méthode.
    if len(toks) >= 7 and not re.search(r"[A-Z][a-z]+[A-Z]|[A-Z]{2,}[a-z]+|\d|[-_/]", clean_text(value)):
        return True
    return False


def _has_specific_modifier_before_role(value: Any) -> bool:
    """
    Règle multi-domaine : une expression générique peut devenir exploitable si elle est qualifiée.
    Ex. "synthetic data augmentation", "bayesian neural network", "sparse signal model".
    On ne hardcode pas le domaine, on vérifie juste qu'il y a un modificateur significatif
    avant un mot de rôle technique.
    """
    toks = _norm_tokens(value)
    if len(toks) < 3:
        return False
    role_terms = {
        "method", "methods", "approach", "approaches", "model", "models", "framework", "algorithm",
        "augmentation", "classification", "detection", "segmentation", "estimation", "prediction",
        "network", "networks", "representation", "optimization", "generation", "selection",
        "méthode", "méthodes", "modèle", "modèles", "approche", "approches",
    }
    generic_fillers = {"data", "image", "images", "signal", "signals", "deep", "learning", "neural"}
    for i, tok in enumerate(toks):
        if tok in role_terms and i > 0:
            modifiers = [x for x in toks[:i] if x not in GENERIC_METHOD_NAMES and x not in generic_fillers and len(x) >= 3]
            if modifiers:
                return True
    return False


def _looks_like_named_method(value: Any) -> bool:
    """
    Test de forme multi-domaine : accepte les noms propres, acronymes contextualisés,
    expressions composées spécifiques, ou concepts qualifiés. Ne contient aucune blacklist domaine.
    """
    raw = clean_text(value)
    if not raw:
        return False

    tokens = _tokens(raw)
    norm = normalize_for_match(raw)
    if not tokens or norm in GENERIC_METHOD_NAMES:
        return False
    if _is_bad_title_phrase(raw):
        return False

    # Nom propre / CamelCase / chiffres / tirets : AdvMask, TerraGen, AeroGen, BERT-large, U-Net, QLoRA...
    if re.search(r"[A-Z][a-z]+[A-Z]|[A-Z]{2,}[a-z]+|\d|[-_/]", raw):
        # Attention : un adjectif technique tronqué comme "Data-Efficient" doit être expansé via le titre si possible.
        return True

    # Acronyme court : accepté seulement si la fonction appelante confirme le contexte.
    if re.fullmatch(r"[A-Z]{2,8}", raw):
        return True

    meaningful = [t for t in _norm_tokens(raw) if t not in GENERIC_METHOD_NAMES and len(t) >= 3]
    if len(meaningful) >= 2:
        return True

    if _has_specific_modifier_before_role(raw):
        return True

    return False


def _title_starts_as_named_method(candidate: Any, title: Any) -> bool:
    c = clean_text(candidate)
    t = clean_text(title)
    if not c or not t:
        return False
    return bool(re.match(rf"^\s*{re.escape(c)}\s*[:\-–—]", t, flags=re.IGNORECASE))


def _candidate_is_title_tail_domain(candidate: Any, title: Any) -> bool:
    c_norm = normalize_for_match(candidate)
    t_norm = normalize_for_match(title)
    if not c_norm or not t_norm:
        return False
    toks = t_norm.split()
    c_toks = c_norm.split()
    if not toks or not c_toks:
        return False

    n = len(c_toks)
    if n <= 2 and toks[-n:] == c_toks:
        before = toks[-n-1] if len(toks) > n else ""
        return before in TITLE_CONNECTORS
    return False




def _is_isolated_acronym(value: Any) -> bool:
    raw = clean_text(value)
    return bool(re.fullmatch(r"[A-Z]{2,8}", raw))


def _initials_match(acronym: str, phrase: str) -> bool:
    ac = clean_text(acronym).upper()
    words = [w for w in re.findall(r"[A-Za-zÀ-ÿ]+", clean_text(phrase)) if len(w) >= 2]
    if not ac or len(words) < 2:
        return False
    initials = "".join(w[0].upper() for w in words)
    return initials == ac


def _extract_acronym_expansion_from_text(acronym: Any, text: Any) -> str:
    """
    Retrouve une expansion possible d'un acronyme à partir du titre/contexte, sans liste domaine.
    Ex. UQ + "uncertainty quantification" -> "Uncertainty Quantification / UQ".
    """
    ac = clean_text(acronym).upper()
    src = clean_text(text)
    if not _is_isolated_acronym(ac) or not src:
        return ""

    # 1) Forme explicite : Full Name (ACR) ou Full Name - ACR
    pat = rf"([A-ZÀ-ÿ][A-Za-zÀ-ÿ\-]+(?:\s+[A-Za-zÀ-ÿ\-]+){{1,5}})\s*[\(\[]\s*{re.escape(ac)}\s*[\)\]]"
    for m in re.finditer(pat, src, flags=re.IGNORECASE):
        phrase = _clean_title_candidate(m.group(1))
        if _initials_match(ac, phrase) and not _is_bad_title_phrase(phrase):
            return f"{phrase} / {ac}"

    # 2) Fenêtre glissante de 2 à 5 mots dont les initiales correspondent.
    words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\-]*", src)
    for n in range(min(5, len(ac)+2), 1, -1):
        for i in range(0, max(0, len(words)-n+1)):
            phrase = " ".join(words[i:i+n])
            if _initials_match(ac, phrase) and not _is_bad_title_phrase(phrase):
                # éviter les phrases purement narratives de type "A review of"
                phrase = _clean_title_candidate(phrase)
                if len(_norm_tokens(phrase)) >= 2:
                    return f"{phrase} / {ac}"
    return ""


def _title_has_better_method_than_acronym(acronym: Any, title: Any) -> bool:
    """
    Un acronyme isolé en fin de titre peut désigner le problème/domaine/tâche,
    alors que le début du titre porte souvent la méthode. On préfère alors le titre.
    Règle structurelle, pas de mots domaine.
    """
    ac = clean_text(acronym)
    t = clean_text(title)
    if not _is_isolated_acronym(ac) or not t:
        return False
    toks = _norm_tokens(t)
    ac_norm = normalize_for_match(ac)
    if not toks or toks[-1] != ac_norm:
        return False
    title_candidate = extract_method_candidate_from_title(t)
    if title_candidate and normalize_for_match(title_candidate) != ac_norm:
        return True
    return False

def _expand_candidate_from_title(candidate: Any, title: Any) -> str:
    """
    Si l'extraction donne un fragment trop court mais que le titre contient une expression plus complète,
    on l'élargit de manière générique jusqu'au connecteur suivant.
    Ex. raw="Data-Efficient" + title="Data-Efficient Augmentation for ..."
        -> "Data-Efficient Augmentation".
    """
    c = clean_text(candidate)
    t = clean_text(title)
    if not c or not t:
        return ""
    m = re.search(re.escape(c), t, flags=re.IGNORECASE)
    if not m:
        return ""
    after = t[m.start():]
    stop = re.search(r"\b(for|in|on|with|using|via|through|pour|dans|sur|avec|par)\b", after, flags=re.IGNORECASE)
    phrase = after[:stop.start()] if stop else after
    phrase = _clean_title_candidate(phrase)
    if phrase and len(_norm_tokens(phrase)) > len(_norm_tokens(c)) and len(phrase) <= 90:
        if not _is_bad_title_phrase(phrase):
            return phrase
    return ""


def _context_supports_method_name(candidate: Any, *, title: Any = "", principle: Any = "", mechanism: Any = "") -> bool:
    raw = clean_text(candidate)
    if not raw:
        return False

    norm = normalize_for_match(raw)
    if norm in GENERIC_METHOD_NAMES:
        return False
    if _is_bad_title_phrase(raw):
        return False

    if _title_starts_as_named_method(raw, title):
        return True

    if _candidate_is_title_tail_domain(raw, title):
        context = normalize_for_match(f"{principle} {mechanism}")
        c = normalize_for_match(raw)
        method_near = bool(re.search(rf"\b({METHOD_ROLE_WORD_RE})\b.{{0,60}}\b{re.escape(c)}\b", context))
        method_near = method_near or bool(re.search(rf"\b{re.escape(c)}\b.{{0,60}}\b({METHOD_ROLE_WORD_RE})\b", context))
        if not method_near:
            return False

    # Acronyme isolé : accepté si le contexte le relie à une méthode/modèle/concept ou si le principe l'analyse clairement.
    if re.fullmatch(r"[A-Z]{2,8}", raw):
        context = normalize_for_match(f"{title} {principle} {mechanism}")
        c = normalize_for_match(raw)
        if _title_starts_as_named_method(raw, title):
            return True
        window_ok = bool(re.search(rf"\b{re.escape(c)}\b.{{0,80}}\b({METHOD_ROLE_WORD_RE}|concept|technique|uncertainty|quantification)\b", context))
        window_ok = window_ok or bool(re.search(rf"\b({METHOD_ROLE_WORD_RE}|concept|technique|uncertainty|quantification)\b.{{0,80}}\b{re.escape(c)}\b", context))
        repeated = len(re.findall(rf"\b{re.escape(c)}\b", context)) >= 2
        return window_ok or repeated

    if _has_specific_modifier_before_role(raw):
        return True

    return _looks_like_named_method(raw)


def is_generic_method_name(value: Any) -> bool:
    s = normalize_for_match(value)
    if not s:
        return True
    if s in GENERIC_METHOD_NAMES:
        return True
    raw = clean_text(value)
    if _is_bad_title_phrase(raw):
        return True
    tokens = _norm_tokens(raw)
    if len(tokens) == 1:
        if raw.islower() and not re.search(r"\d|[-_/]", raw):
            return True
    return False


def _clean_title_candidate(candidate: str) -> str:
    cand = clean_text(candidate)
    cand = re.sub(r"^(a|an|the|une|un|le|la|les)\s+", "", cand, flags=re.IGNORECASE)
    cand = re.sub(r"\s+", " ", cand).strip(" :-–—,.;")
    return cand


def extract_method_candidate_from_title(title: Any) -> str:
    """
    Extraction générique depuis le titre, sans patron de domaine.
    Priorité : nom introduit avant ':' puis segment technique avant connecteur.
    Rejette les débuts de titres narratifs : Exploring/Survey/Review/Study...
    """
    t = clean_text(title)
    if not t:
        return ""

    m = re.match(r"^\s*([A-Z][A-Za-z0-9_\-]{2,45})\s*[:\-–—]\s+", t)
    if m:
        cand = _clean_title_candidate(m.group(1))
        if cand and _looks_like_named_method(cand):
            return cand

    split_re = r"\b(for|in|on|with|using|via|through|pour|dans|sur|avec|par)\b"
    first = re.split(split_re, t, maxsplit=1, flags=re.IGNORECASE)[0]
    first = _clean_title_candidate(first)
    if first and len(first) <= 90 and not _is_bad_title_phrase(first):
        first_tokens = set(_norm_tokens(first))
        has_role_hint = bool(first_tokens & METHOD_ROLE_HINTS)
        has_named_form = bool(re.search(r"[A-Z][a-z]+[A-Z]|[A-Z]{2,}[a-z]+|\d|[-_/]", first))
        has_specific_role = _has_specific_modifier_before_role(first)
        if (has_role_hint or has_named_form or has_specific_role) and not is_generic_method_name(first):
            return first

    candidates = re.findall(r"\b[A-Z][A-Za-z0-9]*(?:[A-Z][A-Za-z0-9]+|\d|[-_/][A-Za-z0-9]+)[A-Za-z0-9_\-/]*\b", t)
    for cand in candidates:
        cand = _clean_title_candidate(cand)
        if cand and _looks_like_named_method(cand) and not _candidate_is_title_tail_domain(cand, t):
            return cand

    return ""


def normalize_method_name_with_fallback(
    *,
    raw_method_name: Any,
    technical_family: Any = "",
    title: Any = "",
    technical_principle: Any = "",
    mechanism: Any = "",
) -> Dict[str, Any]:
    """
    Validation générique multi-domaine V1.7.3.
    - Pas de blacklist domaine.
    - Si le nom brut est un acronyme isolé mais que le titre contient une méthode plus explicite,
      on préfère le titre.
    - Si l'acronyme correspond à une expansion dans le titre/contexte, on garde "Expansion / ACR".
    - Si le titre est du type "NomMéthode: description...", on garde seulement "NomMéthode".
    - On coupe les extensions après for/with/in/etc. pour éviter de mélanger méthode et contexte applicatif.
    """
    raw = clean_text(raw_method_name)
    title_clean = clean_text(title)
    context_text = f"{title_clean} {technical_principle} {mechanism}"

    # 1) Cas très important : un nom propre avant ':' est déjà le nom de méthode.
    #    Ex. AdvMask: ... -> AdvMask ; TerraGen: ... -> TerraGen ; AeroGen: ... -> AeroGen.
    if raw and _title_starts_as_named_method(raw, title_clean) and _looks_like_named_method(raw):
        return {
            "method_name": raw,
            "concept_label": raw,
            "method_name_valid": True,
            "extraction_warning": "",
        }

    # 2) Acronyme isolé : avant de l'accepter, vérifier s'il existe une meilleure méthode dans le titre.
    #    Ex. raw=tâche générique avec titre spécialisé -> nom spécifique extrait du titre.
    if _is_isolated_acronym(raw) and _title_has_better_method_than_acronym(raw, title_clean):
        title_candidate = extract_method_candidate_from_title(title_clean)
        if title_candidate and _context_supports_method_name(title_candidate, title=title_clean, principle=technical_principle, mechanism=mechanism):
            return {
                "method_name": title_candidate,
                "concept_label": title_candidate,
                "method_name_valid": True,
                "extraction_warning": f"method_name remplacé depuis le titre car acronyme isolé faible: {raw}",
            }

    # 3) Acronyme isolé avec expansion textuelle claire.
    #    Ex. UQ -> Uncertainty Quantification / UQ.
    if _is_isolated_acronym(raw):
        expansion = _extract_acronym_expansion_from_text(raw, context_text)
        if expansion:
            return {
                "method_name": expansion,
                "concept_label": expansion,
                "method_name_valid": True,
                "extraction_warning": f"acronyme enrichi par expansion contextuelle: {raw}",
            }

    # 4) Fragment court non-acronyme : expansion contrôlée depuis le titre.
    #    Ex. Data-Efficient -> Data-Efficient Augmentation.
    expanded = _expand_candidate_from_title(raw, title_clean)
    if expanded and _context_supports_method_name(expanded, title=title_clean, principle=technical_principle, mechanism=mechanism):
        return {
            "method_name": expanded,
            "concept_label": expanded,
            "method_name_valid": True,
            "extraction_warning": f"method_name élargi depuis le titre: {raw}",
        }

    # 5) Nom brut acceptable.
    if raw and _context_supports_method_name(raw, title=title_clean, principle=technical_principle, mechanism=mechanism):
        return {
            "method_name": raw,
            "concept_label": raw,
            "method_name_valid": True,
            "extraction_warning": "",
        }

    # 6) Fallback titre : méthode extraite depuis le début du titre.
    title_candidate = extract_method_candidate_from_title(title_clean)
    if title_candidate and _context_supports_method_name(title_candidate, title=title_clean, principle=technical_principle, mechanism=mechanism):
        return {
            "method_name": title_candidate,
            "concept_label": title_candidate,
            "method_name_valid": True,
            "extraction_warning": f"method_name remplacé depuis le titre car extraction faible: {raw}",
        }

    # 7) Fallback famille technique, si elle est exploitable.
    family = clean_text(technical_family)
    if family and not is_placeholder_text(family):
        return {
            "method_name": "",
            "concept_label": family,
            "method_name_valid": False,
            "extraction_warning": f"method_name rejeté par validation contextuelle générique: {raw}",
        }

    short_title = truncate(title_clean, 120)
    return {
        "method_name": "",
        "concept_label": short_title or "concept non nommé",
        "method_name_valid": False,
        "extraction_warning": f"method_name rejeté par validation contextuelle générique: {raw}",
    }

def evidence_overlap_score(a: Any, b: Any) -> float:
    ta = tokenize_for_overlap(a)
    tb = tokenize_for_overlap(b)
    if not ta or not tb:
        return 0.0
    return round(len(ta & tb) / max(1, min(len(ta), len(tb))), 3)


def is_same_or_close_family(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    if clean_text(a.get("family_id")) and clean_text(a.get("family_id")) == clean_text(b.get("family_id")):
        return True

    fa = clean_text(a.get("family_label") or a.get("technical_family"))
    fb = clean_text(b.get("family_label") or b.get("technical_family"))
    if not fa or not fb:
        return False

    return evidence_overlap_score(fa, fb) >= 0.35


def pair_comparability_score(a: Dict[str, Any], b: Dict[str, Any], basis: List[str]) -> float:
    score = 0.0

    same_family = is_same_or_close_family(a, b)
    if same_family:
        score += 3.0

    if a.get("usage_type") == "direct_evidence" and b.get("usage_type") == "direct_evidence":
        score += 2.0
    elif "direct_evidence" in {a.get("usage_type"), b.get("usage_type")}:
        score += 1.0

    strong_basis = [x for x in basis if x not in {"chronology_year_only", "evidence_weight"}]
    score += min(4.0, len(strong_basis) * 0.8)

    text_a = article_evidence_text(a)
    text_b = article_evidence_text(b)
    score += min(2.0, evidence_overlap_score(text_a, text_b) * 8.0)

    # Pénaliser les faux noms de méthode pour éviter A3 "Techniques", A9 "Exploring", etc.
    if not clean_text(a.get("method_name")) or not clean_text(b.get("method_name")):
        score -= 0.8

    if is_placeholder_text(" ".join(as_list(a.get("concept_limits")))) or is_placeholder_text(" ".join(as_list(b.get("concept_limits")))):
        score -= 0.6

    return round(max(0.0, score), 2)


def classify_limit_type(limit: Any) -> str:
    s = normalize_for_match(limit)

    if any(k in s for k in ["dataset", "data", "donnees", "données", "training data", "real images", "labeled data"]):
        return "data_dependency"

    if any(k in s for k in ["benchmark", "protocol", "protocole", "metric", "metrique", "métrique", "validation"]):
        return "protocol_validation"

    if any(k in s for k in ["robust", "robustesse", "generalization", "generalisation", "généralisation", "corruption", "noise", "bruit"]):
        return "robustness_generalization"

    if any(k in s for k in ["transpos", "applicable", "specific", "spécifique", "specifique", "domain", "domaine", "scenario", "scénario"]):
        return "transposability"

    if any(k in s for k in ["comput", "complex", "complexité", "complexite", "hyperparameter", "hyperparam", "cost", "coût", "cout"]):
        return "complexity_parameters"

    if any(k in s for k in ["theoretical", "théorique", "theorique", "understanding", "unclear", "not fully capture"]):
        return "theoretical_understanding"

    return "generic_technical_limit"


def build_causal_consequence(limit_type: str) -> str:
    mapping = {
        "data_dependency": (
            "La limite porte sur la dépendance aux données d'entraînement ; elle rend nécessaire "
            "une vérification de la représentativité, de la diversité et du biais potentiel des données utilisées."
        ),
        "protocol_validation": (
            "La limite concerne le protocole d'évaluation ; elle impose de redéfinir des métriques et scénarios de validation "
            "adaptés au verrou étudié avant toute conclusion de performance."
        ),
        "robustness_generalization": (
            "La limite concerne la robustesse et la généralisation ; elle empêche d'extrapoler les résultats à des conditions "
            "réelles ou dégradées sans essais complémentaires."
        ),
        "transposability": (
            "La limite concerne la transposition de la méthode ; elle indique que le passage vers le contexte du dossier "
            "n'est pas automatique et nécessite adaptation puis validation."
        ),
        "complexity_parameters": (
            "La limite concerne la complexité, les paramètres ou le coût de mise en œuvre ; elle peut modifier la faisabilité "
            "ou la stabilité de la méthode dans un cadre opérationnel."
        ),
        "theoretical_understanding": (
            "La limite est conceptuelle ; elle révèle que les mécanismes expliquant les gains observés ne sont pas suffisamment "
            "établis pour garantir une exploitation directe."
        ),
        "generic_technical_limit": (
            "La limite identifie une incertitude technique qui doit être reformulée et vérifiée dans le protocole propre au projet."
        ),
    }
    return mapping.get(limit_type, mapping["generic_technical_limit"])


def build_project_impact(limit_type: str, verrou_title: str) -> str:
    mapping = {
        "data_dependency": (
            f"Pour le verrou « {verrou_title} », cela maintient une incertitude sur la capacité des données disponibles "
            "à couvrir les cas réels nécessaires à une classification fiable."
        ),
        "protocol_validation": (
            f"Pour le verrou « {verrou_title} », cela signifie que l'efficacité ne peut pas être affirmée sans protocole "
            "d'essai propre au projet et sans critères de décision explicites."
        ),
        "robustness_generalization": (
            f"Pour le verrou « {verrou_title} », cela laisse ouverte la question de la tenue des performances face aux variations, "
            "bruits, corruptions ou conditions d'acquisition."
        ),
        "transposability": (
            f"Pour le verrou « {verrou_title} », cela confirme que l'approche reste un appui scientifique mais ne constitue pas "
            "une solution directement transférable."
        ),
        "complexity_parameters": (
            f"Pour le verrou « {verrou_title} », cela impose d'étudier les réglages, compromis et contraintes de mise en œuvre "
            "avant de pouvoir stabiliser la méthode."
        ),
        "theoretical_understanding": (
            f"Pour le verrou « {verrou_title} », cela justifie un travail R&D pour comprendre pourquoi l'augmentation améliore, "
            "dégrade ou stabilise la classification selon les cas."
        ),
        "generic_technical_limit": (
            f"Pour le verrou « {verrou_title} », cette incertitude doit être rattachée à un essai ou une analyse complémentaire "
            "avant toute conclusion."
        ),
    }
    return mapping.get(limit_type, mapping["generic_technical_limit"])

def clean_method_name(value: Any) -> str:
    """
    V1.4 :
    Nettoie le nom de méthode et rejette les faux mots-clés NER.
    Ne pas utiliser seul si tu as le titre : préférer normalize_method_name_with_fallback().
    """
    name = clean_text(value)
    if not name:
        return ""

    if is_generic_method_name(name):
        return ""

    return name


def build_single_technical_method_reasoning(
    *,
    verrou_gap: Dict[str, Any],
    card: Dict[str, Any],
    citation: str,
    usage_type: str,
    usage_label: str,
    weight: Dict[str, Any],
) -> Dict[str, Any]:
    technical = card.get("technical_method_analysis") or {}

    technical_family = clean_text(
        technical.get("technical_family")
        or card.get("technical_family")
    )

    principle = clean_text(
        technical.get("principle")
        or technical.get("technical_principle")
        or card.get("technical_principle")
        or card.get("method")
    )

    mechanism = clean_text(
        technical.get("mechanism")
        or card.get("mechanism")
        or card.get("method")
    )

    method_norm = normalize_method_name_with_fallback(
        raw_method_name=(
            technical.get("method_name")
            or card.get("method_name")
        ),
        technical_family=technical_family,
        title=card.get("title"),
        technical_principle=principle,
        mechanism=mechanism,
    )

    method_name = method_norm.get("method_name")
    concept_label = method_norm.get("concept_label")

    dataset_context = clean_text(
        technical.get("dataset_context")
        or card.get("dataset_context")
    )

    validation_protocol = clean_text(
        technical.get("validation_protocol")
        or card.get("validation_protocol")
    )

    reported_results = clean_text(
        technical.get("reported_results")
        or card.get("reported_results")
        or card.get("results")
    )

    problem_context = clean_text(
        technical.get("problem_context")
        or card.get("problem_context")
        or card.get("abstract")
    )

    solution_context = clean_text(
        technical.get("solution_context")
        or card.get("solution_context")
    )

    concept_limits = clean_evidence_items(
        technical.get("concept_limits")
        or card.get("concept_limits")
        or [],
        max_items=5,
    )

    transposability_limits = clean_evidence_items(
        technical.get("transposability_limits")
        or card.get("transposability_limits")
        or [],
        max_items=5,
    )

    impact = clean_text(
        technical.get("impact_on_verrou")
        or card.get("impact_on_verrou")
        or card.get("relevance")
    )
    if is_weak_generic_impact(impact):
        impact = ""

    remaining_uncertainty = clean_text(
        technical.get("remaining_uncertainty")
        or card.get("remaining_uncertainty")
    )
    if is_weak_generic_impact(remaining_uncertainty) or is_placeholder_text(remaining_uncertainty):
        remaining_uncertainty = ""

    cir_exploitation = clean_text(
        technical.get("cir_exploitation")
        or card.get("cir_exploitation")
    )

    phase2_context = clean_text(
        technical.get("phase2_evidence_for_reasoning")
        or card.get("phase2_evidence_for_reasoning")
    )

    evidence_extracts = technical.get("evidence_extracts") or card.get("evidence_extracts_for_reasoning") or {}

    technical_detail_profile = build_technical_detail_profile(card=card, technical=technical)

    has_real_principle = has_substantive_text(principle, min_tokens=7, min_chars=55)
    has_real_mechanism = has_substantive_text(mechanism, min_tokens=7, min_chars=55)
    has_real_dataset = has_substantive_text(dataset_context, min_tokens=5, min_chars=40)
    has_real_validation = has_substantive_text(validation_protocol, min_tokens=5, min_chars=40)
    has_real_results = has_substantive_text(reported_results, min_tokens=5, min_chars=40)
    has_real_limits = bool(concept_limits or transposability_limits)
    has_real_impact = has_substantive_text(impact, min_tokens=6, min_chars=45)

    usage_type = normalize_usage_type(usage_type)

    if usage_type == "direct_evidence":
        phase_5_usage = (
            "Méthode/concept directement lié au verrou. À expliquer techniquement avec son principe, "
            "son mécanisme, ses données/protocole de validation et ses limites conceptuelles. "
            "Peut servir de preuve centrale si le poids est élevé."
        )
    elif usage_type == "related_evidence":
        phase_5_usage = (
            "Méthode/concept connexe. À utiliser uniquement comme éclairage méthodologique ou pour discuter "
            "la transposabilité, sans l'assimiler à une preuve directe."
        )
    elif usage_type == "methodological_evidence":
        phase_5_usage = (
            "Appui méthodologique. À utiliser pour enrichir l'explication d'un principe, d'un protocole ou d'une limite, "
            "pas comme preuve centrale du verrou."
        )
    else:
        phase_5_usage = (
            "Élément de contexte conservé car sélectionné. À utiliser avec prudence et faible poids."
        )

    return {
        "citation_label": citation,
        "usage_type": usage_type,
        "usage_label": usage_label,
        "priority_score": weight.get("score"),
        "priority_tier": weight.get("tier"),
        "weight_factors": weight.get("factors") or [],
        "quality_status": card.get("quality_status"),
        "full_text_available": bool(card.get("full_text_available")),
        "article_evidence_bank_available": bool(card.get("article_evidence_bank_available")),
        "article_evidence_bucket_counts": card.get("article_evidence_bucket_counts") or {},
        "generation_mode": card.get("generation_mode"),
        "source_availability": (
            "extractive_fulltext_evidence_bank"
            if card.get("article_evidence_bank_available")
            else ("fulltext" if card.get("full_text_available") else "abstract_or_metadata_only")
        ),
        "method_name": method_name,
        "concept_label": concept_label,
        "method_name_valid": bool(method_norm.get("method_name_valid")),
        "extraction_warning": method_norm.get("extraction_warning"),
        "technical_family": technical_family,
        "technical_principle": truncate(principle, 1100) if has_real_principle else "",
        "mechanism": truncate(mechanism, 1200) if has_real_mechanism else "",
        "dataset_context": truncate(dataset_context, 850) if has_real_dataset else "",
        "validation_protocol": truncate(validation_protocol, 900) if has_real_validation else "",
        "reported_results": truncate(reported_results, 850) if has_real_results else "",
        "problem_context": truncate(problem_context, 850),
        "solution_context": truncate(solution_context, 850),
        "concept_limits": [truncate(x, 450) for x in concept_limits],
        "transposability_limits": [truncate(x, 450) for x in transposability_limits],
        "impact_on_verrou": truncate(impact, 700) if has_real_impact else "",
        "remaining_uncertainty": truncate(remaining_uncertainty, 700),
        "cir_exploitation": truncate(cir_exploitation, 700),
        "phase2_evidence_for_reasoning": truncate(phase2_context, 5200),
        "evidence_extracts_for_reasoning": evidence_extracts,
        "technical_detail_profile": technical_detail_profile,
        "architecture_details": technical_detail_profile.get("architecture") or {},
        "training_hyperparameters": technical_detail_profile.get("training_hyperparameters") or {},
        "data_and_protocol_details": technical_detail_profile.get("data_and_protocol") or {},
        "evaluation_metrics_details": technical_detail_profile.get("evaluation_metrics") or {},
        "implementation_parameters": technical_detail_profile.get("implementation_parameters") or {},
        "missing_technical_details": technical_detail_profile.get("missing_details") or [],
        "technical_detail_summary_for_phase_5": summarize_technical_detail_profile(technical_detail_profile),
        "technical_available": bool(
            technical_detail_profile.get("has_any_detail")
            or has_real_principle
            or has_real_mechanism
            or has_real_dataset
            or has_real_validation
            or has_real_results
            or has_real_limits
            or has_real_impact
        ),
        "phase_5_usage_instruction": phase_5_usage,
        "no_article_ignored": True,
    }


def build_technical_methods_reasoning(
    *,
    verrou_gap: Dict[str, Any],
    families: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    items = []

    for family in families:
        for tm in family.get("technical_methods") or []:
            enriched = dict(tm)
            enriched["family_id"] = family.get("family_id")
            enriched["family_label"] = family.get("family_label")
            enriched["family_evidence_strength"] = family.get("evidence_strength")
            items.append(enriched)

    items.sort(
        key=lambda x: (
            0 if x.get("usage_type") == "direct_evidence" else 1,
            -safe_float(x.get("priority_score"), 0.0),
            x.get("citation_label") or "",
        )
    )

    return items


def build_concept_limit_matrix(
    *,
    technical_methods: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    matrix = []

    for item in technical_methods:
        matrix.append({
            "citation_label": item.get("citation_label"),
            "family_label": item.get("family_label"),
            "usage_type": item.get("usage_type"),
            "priority_tier": item.get("priority_tier"),
            "method_or_concept": item.get("method_name") or item.get("technical_family"),
            "technical_principle": item.get("technical_principle"),
            "mechanism": item.get("mechanism"),
            "concept_limits": item.get("concept_limits") or [],
            "transposability_limits": item.get("transposability_limits") or [],
            "impact_on_verrou": item.get("impact_on_verrou"),
            "remaining_uncertainty": item.get("remaining_uncertainty"),
            "technical_detail_profile": item.get("technical_detail_profile") or {},
            "technical_detail_summary_for_phase_5": item.get("technical_detail_summary_for_phase_5"),
            "missing_technical_details": item.get("missing_technical_details") or [],
            "phase_5_instruction": (
                "Expliquer le concept et sa limite. "
                "Montrer pourquoi la limite laisse le verrou ouvert. "
                "Ne pas écrire 'l'article [A] présente'."
            ),
        })

    return matrix



# ============================================================
# Consultant reasoning enrichment V1.3
# ============================================================

def extract_year_int(value: Any) -> Optional[int]:
    s = clean_text(value)
    m = re.search(r"\b(19|20)\d{2}\b", s)
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None


def article_evidence_text(card_or_method: Dict[str, Any]) -> str:
    """
    Texte d'évidence contrôlé.
    V1.8 : donne la priorité aux preuves extractives Phase 2 V3.2.
    """
    return clean_text(" ".join([
        clean_text(card_or_method.get("title")),
        clean_text(card_or_method.get("abstract")),
        clean_text(card_or_method.get("method")),
        clean_text(card_or_method.get("results")),
        clean_text(card_or_method.get("limitations")),
        clean_text(card_or_method.get("relevance")),
        clean_text(card_or_method.get("technical_principle")),
        clean_text(card_or_method.get("mechanism")),
        clean_text(card_or_method.get("dataset_context")),
        clean_text(card_or_method.get("validation_protocol")),
        clean_text(card_or_method.get("reported_results")),
        clean_text(card_or_method.get("problem_context")),
        clean_text(card_or_method.get("solution_context")),
        clean_text(card_or_method.get("phase2_evidence_for_reasoning")),
        " ".join(clean_text(x) for x in as_list(card_or_method.get("concept_limits"))),
        " ".join(clean_text(x) for x in as_list(card_or_method.get("transposability_limits"))),
        clean_text(card_or_method.get("impact_on_verrou")),
        clean_text(card_or_method.get("remaining_uncertainty")),
    ]))


def detect_evidence_basis(a: Dict[str, Any], b: Dict[str, Any]) -> List[str]:
    """
    V1.4 — Evidence basis plus stricte.
    Le fait qu'un champ existe ne suffit plus : il doit être substantiel et non placeholder.
    """
    basis = []

    if is_same_or_close_family(a, b):
        basis.append("same_or_close_family")

    if (
        has_substantive_text(a.get("technical_principle"), min_tokens=7, min_chars=55)
        and has_substantive_text(b.get("technical_principle"), min_tokens=7, min_chars=55)
        and evidence_overlap_score(a.get("technical_principle"), b.get("technical_principle")) >= 0.08
    ):
        basis.append("principle_overlap")

    if (
        has_substantive_text(a.get("mechanism"), min_tokens=7, min_chars=55)
        and has_substantive_text(b.get("mechanism"), min_tokens=7, min_chars=55)
        and evidence_overlap_score(a.get("mechanism"), b.get("mechanism")) >= 0.08
    ):
        basis.append("mechanism_overlap")

    a_limits = " ".join(clean_evidence_items(a.get("concept_limits") or []))
    b_limits = " ".join(clean_evidence_items(b.get("concept_limits") or []))
    if a_limits and b_limits and evidence_overlap_score(a_limits, b_limits) >= 0.06:
        basis.append("concept_limits_overlap")

    a_trans = " ".join(clean_evidence_items(a.get("transposability_limits") or []))
    b_trans = " ".join(clean_evidence_items(b.get("transposability_limits") or []))
    if a_trans and b_trans and evidence_overlap_score(a_trans, b_trans) >= 0.06:
        basis.append("transposability_overlap")

    if (
        has_substantive_text(a.get("impact_on_verrou"), min_tokens=6, min_chars=45)
        and has_substantive_text(b.get("impact_on_verrou"), min_tokens=6, min_chars=45)
        and not is_weak_generic_impact(a.get("impact_on_verrou"))
        and not is_weak_generic_impact(b.get("impact_on_verrou"))
    ):
        basis.append("specific_impact_on_verrou")

    if clean_text(a.get("priority_tier")) and clean_text(b.get("priority_tier")):
        basis.append("evidence_weight")

    ya = extract_year_int(a.get("year"))
    yb = extract_year_int(b.get("year"))
    if ya and yb and ya != yb:
        basis.append("chronology_year_only")

    return basis


def build_comparison_guard(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """
    Comparaison anti-hallucination V1.4.
    On ne compare plus tout avec tout :
    - direct : même famille/proximité + plusieurs bases solides ;
    - cautious : relation scientifique raisonnable mais partielle ;
    - no_comparison : séparé.
    """
    basis = detect_evidence_basis(a, b)
    same_family = is_same_or_close_family(a, b)
    score = pair_comparability_score(a, b, basis)

    solid_basis = [
        x for x in basis
        if x not in {"chronology_year_only", "evidence_weight"}
    ]

    both_direct = (
        normalize_usage_type(a.get("usage_type")) == "direct_evidence"
        and normalize_usage_type(b.get("usage_type")) == "direct_evidence"
    )

    if same_family and both_direct and len(solid_basis) >= 4 and score >= 7.0:
        comparison_type = "direct_comparison"
        allowed = (
            "Comparer directement les deux approches uniquement sur les dimensions explicitement communes "
            "et sourcées : principe, mécanisme, limites ou transposabilité. Ne pas affirmer de supériorité."
        )
    elif same_family and len(solid_basis) >= 3 and score >= 5.8:
        comparison_type = "cautious_comparison"
        allowed = (
            "Présenter ces travaux comme deux variantes ou éclairages d'une même famille scientifique. "
            "La comparaison doit rester qualitative et limitée aux dimensions explicitement présentes."
        )
    elif len(solid_basis) >= 4 and score >= 7.2 and both_direct:
        comparison_type = "cautious_comparison"
        allowed = (
            "Présenter ces travaux comme complémentaires, pas comme directement concurrents. "
            "Éviter toute hiérarchie de performance ou d'évolution méthodologique non prouvée."
        )
    else:
        comparison_type = "no_comparison"
        allowed = (
            "Ne pas comparer directement. Utiliser les deux références séparément dans leur rôle respectif."
        )

    forbidden = [
        "Ne pas dire qu'une méthode améliore l'autre sans résultat comparatif explicite.",
        "Ne pas dire qu'une méthode est plus robuste, plus précise ou plus performante sans métrique exploitable.",
        "Ne pas construire une chronologie ancienne/nouvelle uniquement à partir des dates.",
        "Ne pas transformer une citation connexe en preuve directe.",
        "Ne pas comparer deux articles uniquement parce qu'ils possèdent des champs remplis dans l'Article Card.",
    ]

    return {
        "a": a.get("citation_label"),
        "b": b.get("citation_label"),
        "a_method_or_family": a.get("concept_label") or a.get("method_name") or a.get("technical_family") or a.get("family_label"),
        "b_method_or_family": b.get("concept_label") or b.get("method_name") or b.get("technical_family") or b.get("family_label"),
        "comparison_type": comparison_type,
        "comparability_score": score,
        "evidence_basis": basis,
        "same_family": same_family,
        "allowed_comparison_instruction": allowed,
        "forbidden_claims": forbidden,
    }


def build_cross_article_comparison(
    *,
    technical_methods: List[Dict[str, Any]],
    max_pairs: int = 6,
) -> List[Dict[str, Any]]:
    """
    Crée une matrice de comparaison contrôlée V1.4.
    Limite volontaire : 5–8 comparaisons fortes maximum.
    """
    candidates = [
        x for x in technical_methods
        if x.get("priority_tier") in {"core", "important"}
        and x.get("technical_available")
    ]

    candidates = sorted(
        candidates,
        key=lambda x: (
            0 if x.get("usage_type") == "direct_evidence" else 1,
            0 if x.get("method_name_valid") else 1,
            -safe_float(x.get("priority_score"), 0.0),
            clean_text(x.get("citation_label")),
        )
    )[:8]

    pairs = []
    for i, a in enumerate(candidates):
        for b in candidates[i + 1:]:
            guard = build_comparison_guard(a, b)
            if guard["comparison_type"] == "no_comparison":
                continue
            pairs.append(guard)

    pairs.sort(
        key=lambda x: (
            0 if x["comparison_type"] == "direct_comparison" else 1,
            -safe_float(x.get("comparability_score"), 0.0),
            -len(x.get("evidence_basis") or []),
            clean_text(x.get("a")),
            clean_text(x.get("b")),
        )
    )

    # Diversification : éviter que A1 soit comparé à tout le monde.
    selected = []
    citation_use_count: Dict[str, int] = {}

    for pair in pairs:
        a = clean_text(pair.get("a"))
        b = clean_text(pair.get("b"))

        if citation_use_count.get(a, 0) >= 2 or citation_use_count.get(b, 0) >= 2:
            continue

        selected.append(pair)
        citation_use_count[a] = citation_use_count.get(a, 0) + 1
        citation_use_count[b] = citation_use_count.get(b, 0) + 1

        if len(selected) >= max_pairs:
            break

    return selected


def infer_concept_label_from_method(item: Dict[str, Any]) -> str:
    for key in ["concept_label", "method_name", "technical_family", "family_label"]:
        s = clean_text(item.get(key))
        if s and not is_generic_method_name(s):
            return s
        if key in {"technical_family", "family_label"} and s:
            return s

    title = clean_text(item.get("title"))
    if title:
        title_candidate = extract_method_candidate_from_title(title)
        if title_candidate:
            return title_candidate
        # Ne pas retourner un titre qui commence par un domaine ou une tâche générique.
        words = title.split()
        short = " ".join(words[:8])
        if not is_generic_method_name(short):
            return short

    return "concept non nommé"


def build_concept_deepening(
    *,
    technical_methods: List[Dict[str, Any]],
    max_concepts: int = 0,
) -> List[Dict[str, Any]]:
    """
    Approfondissement sans invention :
    on ne crée une fiche concept que si un principe/mécanisme/limite existe dans les Article Cards.
    """
    concepts = []

    for item in technical_methods:
        principle = clean_text(item.get("technical_principle"))
        mechanism = clean_text(item.get("mechanism"))
        limits = [clean_text(x) for x in as_list(item.get("concept_limits")) if clean_text(x)]
        trans = [clean_text(x) for x in as_list(item.get("transposability_limits")) if clean_text(x)]
        impact = clean_text(item.get("impact_on_verrou"))

        if not (principle or mechanism or limits or trans or impact):
            continue

        concepts.append({
            "concept_label": infer_concept_label_from_method(item),
            "citation_label": item.get("citation_label"),
            "family_label": item.get("family_label"),
            "priority_tier": item.get("priority_tier"),
            "usage_type": item.get("usage_type"),
            "what_can_be_explained": {
                "principle_available": bool(principle),
                "mechanism_available": bool(mechanism),
                "concept_limits_available": bool(limits),
                "transposability_limits_available": bool(trans),
                "impact_on_verrou_available": bool(impact),
            },
            "principle_to_explain": truncate(principle, 700),
            "mechanism_to_explain": truncate(mechanism, 700),
            "limits_to_explain": [truncate(x, 350) for x in limits[:3]],
            "transposability_to_explain": [truncate(x, 350) for x in trans[:3]],
            "impact_on_verrou": truncate(impact, 550),
            "technical_detail_profile": item.get("technical_detail_profile") or {},
            "technical_detail_summary_for_phase_5": item.get("technical_detail_summary_for_phase_5"),
            "missing_technical_details": item.get("missing_technical_details") or [],
            "writer_instruction": (
                "Développer le concept seulement à partir des champs disponibles. "
                "Si un détail manque, ne pas le compléter par connaissance générale non sourcée. "
                "La rédaction doit expliquer le principe puis la limite, pas seulement citer l'article."
            ),
        })

    concepts.sort(
        key=lambda x: (
            {"core": 0, "important": 1, "support": 2, "context_low_confidence": 3}.get(x.get("priority_tier"), 9),
            0 if x.get("usage_type") == "direct_evidence" else 1,
            clean_text(x.get("citation_label")),
        )
    )

    if max_concepts is not None and max_concepts > 0:
        return concepts[:max_concepts]

    return concepts


def build_technical_limit_causality(
    *,
    technical_methods: List[Dict[str, Any]],
    verrou_title: str,
    max_items: int = 12,
) -> List[Dict[str, Any]]:
    """
    Transforme les limites en chaîne cause -> conséquence -> impact verrou.
    V1.4 :
    - supprime les placeholders ;
    - déduplique les limites ;
    - varie les conséquences selon le type de limite.
    """
    rows = []
    seen_causes = set()

    for item in technical_methods:
        limits = []
        limits.extend(clean_evidence_items(item.get("concept_limits") or [], max_items=3))
        limits.extend(clean_evidence_items(item.get("transposability_limits") or [], max_items=3))

        if item.get("remaining_uncertainty") and not is_weak_generic_impact(item.get("remaining_uncertainty")):
            limits.append(clean_text(item.get("remaining_uncertainty")))

        if not limits:
            continue

        for lim in limits[:3]:
            if is_placeholder_text(lim):
                continue

            cause_key = normalize_for_match(lim)
            if not cause_key or cause_key in seen_causes:
                continue
            seen_causes.add(cause_key)

            limit_type = classify_limit_type(lim)

            rows.append({
                "citation_label": item.get("citation_label"),
                "family_label": item.get("family_label"),
                "method_or_concept": item.get("concept_label") or item.get("method_name") or item.get("technical_family") or item.get("family_label"),
                "priority_tier": item.get("priority_tier"),
                "limit_type": limit_type,
                "cause_from_source": truncate(lim, 450),
                "technical_consequence": build_causal_consequence(limit_type),
                "impact_on_current_verrou": build_project_impact(limit_type, verrou_title),
                "writer_instruction": (
                    "Rédiger sous forme cause -> conséquence -> impact projet. "
                    "Adapter le vocabulaire au type de limite. Ne pas répéter une formule générique."
                ),
            })

    rows.sort(
        key=lambda x: (
            {"core": 0, "important": 1, "support": 2, "context_low_confidence": 3}.get(x.get("priority_tier"), 9),
            clean_text(x.get("limit_type")),
            clean_text(x.get("citation_label")),
        )
    )

    return rows[:max_items]


def build_scientific_storyline(
    *,
    verrou_gap: Dict[str, Any],
    families: List[Dict[str, Any]],
    technical_methods: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Mini-histoire scientifique par verrou.
    Elle reste prudente : elle ne prétend pas à une évolution historique si l'évidence ne suffit pas.
    """
    verrou_title = clean_text(verrou_gap.get("verrou_title"))
    core_families = main_family_labels(families, max_items=5)

    dated = []
    for item in technical_methods:
        y = extract_year_int(item.get("year"))
        if y:
            dated.append({
                "citation_label": item.get("citation_label"),
                "year": y,
                "family_label": item.get("family_label"),
                "method_or_concept": item.get("method_name") or item.get("technical_family") or item.get("family_label"),
                "priority_tier": item.get("priority_tier"),
            })

    dated.sort(key=lambda x: (x["year"], clean_text(x["citation_label"])))

    chronology_allowed = len(dated) >= 2
    if chronology_allowed:
        narrative_mode = "chronology_available_but_cautious"
        chronology_instruction = (
            "Une progression chronologique peut être mentionnée uniquement comme ordre de publication. "
            "Ne pas affirmer une amélioration méthode-à-méthode sans comparaison explicite."
        )
    else:
        narrative_mode = "conceptual_storyline_only"
        chronology_instruction = (
            "Ne pas écrire une chronologie. Construire une histoire conceptuelle : problème -> familles -> limites -> verrou."
        )

    return {
        "storyline_type": "per_verrou_scientific_storyline",
        "verrou_title": verrou_title,
        "narrative_mode": narrative_mode,
        "core_families_to_introduce": core_families,
        "chronology": dated[:12],
        "chronology_instruction": chronology_instruction,
        "suggested_story_arc": [
            "Partir du besoin scientifique du verrou et du contexte de validation.",
            "Introduire les familles principales comme réponses partielles au problème.",
            "Expliquer les concepts les plus solides avec leur mécanisme.",
            "Comparer uniquement les approches comparables selon comparison_guard_matrix.",
            "Faire émerger les limites communes et leurs causes techniques.",
            "Conclure sur l'incertitude résiduelle justifiant les travaux R&D.",
        ],
        "writer_instruction": (
            "Phase 5 doit raconter cette progression comme une histoire scientifique continue. "
            "Les articles ne doivent pas être listés un par un."
        ),
    }


def build_demonstration_chain(
    *,
    verrou_gap: Dict[str, Any],
    families: List[Dict[str, Any]],
    insufficiencies: List[Dict[str, Any]],
    limit_causality: List[Dict[str, Any]],
) -> Dict[str, Any]:
    verrou_title = clean_text(verrou_gap.get("verrou_title"))
    family_labels = main_family_labels(families, max_items=5)

    insuff_labels = [
        clean_text(x.get("label"))
        for x in insufficiencies
        if clean_text(x.get("label"))
    ]

    causes = [
        clean_text(x.get("cause_from_source"))
        for x in limit_causality[:5]
        if clean_text(x.get("cause_from_source"))
    ]

    return {
        "demonstration_type": "consultant_cir_scientific_demonstration_chain",
        "verrou_title": verrou_title,
        "chain": [
            {
                "step": "1_state_of_art_assets",
                "claim": "L'état de l'art fournit plusieurs familles d'approches utiles.",
                "evidence": family_labels,
            },
            {
                "step": "2_partial_contribution",
                "claim": "Ces approches apportent des mécanismes, méthodes ou critères exploitables pour cadrer le verrou.",
                "evidence": "Voir technical_methods_reasoning et concept_deepening.",
            },
            {
                "step": "3_observed_limits",
                "claim": "Les limites extraites des sources empêchent une transposition directe.",
                "evidence": causes,
            },
            {
                "step": "4_remaining_uncertainties",
                "claim": "Les insuffisances persistantes portent sur la validation, la généralisation, les données, les paramètres ou le protocole.",
                "evidence": insuff_labels,
            },
            {
                "step": "5_rd_need",
                "claim": "Le verrou reste donc ouvert tant qu'un protocole propre au projet n'a pas testé, adapté et validé ces approches.",
                "evidence": verrou_title,
            },
        ],
        "writer_instruction": (
            "Utiliser cette chaîne comme squelette logique. "
            "Ne pas conclure 'le verrou reste ouvert' avant d'avoir exposé les étapes 1 à 4."
        ),
    }


def build_project_context_bridge_input(
    *,
    verrou_gap: Dict[str, Any],
    technical_methods: List[Dict[str, Any]],
    limit_causality: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Prépare une entrée pour Phase 4.6 sans remplacer Phase 4.6.
    """
    verrou_title = clean_text(verrou_gap.get("verrou_title"))
    objectif_rd = clean_text(verrou_gap.get("objectif_rd") or verrou_title)

    project_sensitive_points = []
    for row in limit_causality[:8]:
        project_sensitive_points.append({
            "citation_label": row.get("citation_label"),
            "method_or_concept": row.get("method_or_concept"),
            "project_risk": row.get("impact_on_current_verrou"),
            "source_limit": row.get("cause_from_source"),
        })

    return {
        "bridge_type": "input_for_phase_4_6_project_context",
        "verrou_title": verrou_title,
        "objectif_rd": objectif_rd,
        "project_sensitive_points": project_sensitive_points,
        "phase_4_6_instruction": (
            "Relier ces limites au contexte métier/projet : contraintes d'usage, données réelles, protocole, "
            "validation expérimentale, robustesse et transposabilité. "
            "Ne pas inventer de contexte opérationnel absent du projet ; formuler prudemment."
        ),
    }


def build_consultant_reasoning_enrichment(
    *,
    verrou_gap: Dict[str, Any],
    families: List[Dict[str, Any]],
    technical_methods: List[Dict[str, Any]],
    insufficiencies: List[Dict[str, Any]],
) -> Dict[str, Any]:
    verrou_title = clean_text(verrou_gap.get("verrou_title"))

    storyline = build_scientific_storyline(
        verrou_gap=verrou_gap,
        families=families,
        technical_methods=technical_methods,
    )

    concept_deepening = build_concept_deepening(
        technical_methods=technical_methods,
    )

    comparison_guard_matrix = build_cross_article_comparison(
        technical_methods=technical_methods,
    )

    technical_limit_causality = build_technical_limit_causality(
        technical_methods=technical_methods,
        verrou_title=verrou_title,
    )

    demonstration_chain = build_demonstration_chain(
        verrou_gap=verrou_gap,
        families=families,
        insufficiencies=insufficiencies,
        limit_causality=technical_limit_causality,
    )

    project_context_bridge_input = build_project_context_bridge_input(
        verrou_gap=verrou_gap,
        technical_methods=technical_methods,
        limit_causality=technical_limit_causality,
    )

    return {
        "enrichment_type": "phase_4_5_v1_3_consultant_reasoning_enrichment",
        "scientific_storyline": storyline,
        "concept_deepening": concept_deepening,
        "comparison_guard_matrix": comparison_guard_matrix,
        "technical_limit_causality": technical_limit_causality,
        "demonstration_chain": demonstration_chain,
        "project_context_bridge_input_for_phase_4_6": project_context_bridge_input,
        "anti_hallucination_rules": {
            "compare_only_if_evidence_basis_exists": True,
            "chronology_does_not_mean_improvement": True,
            "no_superiority_without_metric_or_explicit_result": True,
            "no_business_context_invention": True,
            "all_claims_must_be_grounded_in_article_cards_or_phase4_gap": True,
        },
        "phase_5_global_instruction": (
            "Utiliser ces blocs pour transformer la rédaction en raisonnement consultant : "
            "histoire scientifique, regroupement par familles, comparaison contrôlée, limites causales, "
            "démonstration progressive. Si un bloc est vide, ne pas l'inventer."
        ),
    }


def format_consultant_enrichment_for_prompt(enrichment: Dict[str, Any]) -> str:
    """
    Convertit l'enrichissement en texte compact pour reasoning_block_for_phase_5.
    """
    if not isinstance(enrichment, dict) or not enrichment:
        return ""

    lines = []
    lines.append("ENRICHISSEMENT CONSULTANT V1.3 — À UTILISER AVANT RÉDACTION")
    lines.append("Objectif : transformer les articles en histoire scientifique, comparaison contrôlée et démonstration.")

    storyline = enrichment.get("scientific_storyline") or {}
    if storyline:
        lines.append("\nHISTOIRE SCIENTIFIQUE")
        for step in storyline.get("suggested_story_arc") or []:
            lines.append(f"- {step}")
        if storyline.get("core_families_to_introduce"):
            lines.append("Familles à introduire : " + ", ".join(storyline.get("core_families_to_introduce") or []))
        lines.append("Règle chronologie : " + clean_text(storyline.get("chronology_instruction")))

    concepts = enrichment.get("concept_deepening") or []
    if concepts:
        lines.append("\nCONCEPTS À APPROFONDIR")
        for c in concepts[:8]:
            lines.append(
                f"- [{c.get('citation_label')}] {c.get('concept_label')} : "
                f"principe={bool(c.get('principle_to_explain'))}, "
                f"mécanisme={bool(c.get('mechanism_to_explain'))}, "
                f"limites={len(c.get('limits_to_explain') or [])}"
            )

    comparisons = enrichment.get("comparison_guard_matrix") or []
    if comparisons:
        lines.append("\nCOMPARAISONS AUTORISÉES / PRUDENTES")
        for cmp_item in comparisons[:8]:
            lines.append(
                f"- [{cmp_item.get('a')}] vs [{cmp_item.get('b')}] : "
                f"{cmp_item.get('comparison_type')} | bases={', '.join(cmp_item.get('evidence_basis') or [])}"
            )

    causality = enrichment.get("technical_limit_causality") or []
    if causality:
        lines.append("\nLIMITES À FORMULER EN CAUSE -> CONSÉQUENCE -> IMPACT")
        for row in causality[:8]:
            lines.append(
                f"- [{row.get('citation_label')}] {row.get('method_or_concept')} : "
                f"cause={row.get('cause_from_source')} | impact={row.get('impact_on_current_verrou')}"
            )

    demonstration = enrichment.get("demonstration_chain") or {}
    if demonstration:
        lines.append("\nCHAÎNE DE DÉMONSTRATION")
        for step in demonstration.get("chain") or []:
            lines.append(f"- {step.get('step')} : {step.get('claim')}")

    lines.append("\nRÈGLES ANTI-HALLUCINATION")
    rules = enrichment.get("anti_hallucination_rules") or {}
    for k, v in rules.items():
        lines.append(f"- {k} = {v}")

    return "\n".join(lines).strip()


# ============================================================
# Argumentation profile
# ============================================================

def get_argumentation_profile(argumentation_payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(argumentation_payload, dict):
        return {}

    return argumentation_payload.get("argumentation_profile") or {}


def get_insufficiency_taxonomy(argumentation_profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    taxonomy = argumentation_profile.get("insufficiency_taxonomy") or []
    return [x for x in taxonomy if isinstance(x, dict)]


# ============================================================
# Insufficiency reasoning
# ============================================================

def choose_insufficiencies_for_verrou(
    verrou_gap: Dict[str, Any],
    families: List[Dict[str, Any]],
    technical_methods: List[Dict[str, Any]],
    argumentation_profile: Dict[str, Any],
) -> List[Dict[str, Any]]:
    taxonomy = get_insufficiency_taxonomy(argumentation_profile)

    text = normalize_for_match(
        " ".join([
            verrou_gap.get("verrou_title") or "",
            verrou_gap.get("objectif_rd") or "",
            verrou_gap.get("scientific_gap") or "",
            verrou_gap.get("rd_justification") or "",
            " ".join(verrou_gap.get("non_transposability") or []),
            " ".join(verrou_gap.get("article_limitations") or []),
            " ".join(
                " ".join(tm.get("concept_limits") or [])
                for tm in technical_methods
            ),
            " ".join(
                " ".join(tm.get("transposability_limits") or [])
                for tm in technical_methods
            ),
            " ".join(tm.get("impact_on_verrou") or "" for tm in technical_methods),
        ])
    )

    family_text = normalize_for_match(
        " ".join(f.get("family_label") or "" for f in families)
    )

    selected = []

    def add_by_code(code: str, reason: str):
        for item in taxonomy:
            if item.get("code") == code:
                enriched = dict(item)
                enriched["selection_reason"] = reason
                selected.append(enriched)
                return

    if any(k in text for k in ["validation", "eval", "éval", "preuve", "demontre", "démontre"]):
        add_by_code(
            "validation_incomplete",
            "Le verrou porte sur la validation de l'efficacité et la preuve expérimentale dans le contexte du projet.",
        )

    if any(k in text for k in ["generalisation", "généralisation", "robustesse", "robust", "conditions"]):
        add_by_code(
            "generalisation_limitee",
            "Le gap et les limites conceptuelles mentionnent la généralisation, la robustesse ou des conditions d'usage variables.",
        )

    if any(k in text for k in ["données", "donnees", "data", "représentativité", "representativite", "dataset"]):
        add_by_code(
            "dependance_aux_donnees",
            "Les méthodes identifiées dépendent de la quantité, diversité ou représentativité des données.",
        )

    if any(k in text for k in ["transposition", "applicable", "contexte specifique", "contexte spécifique", "transfer", "non flat"]):
        add_by_code(
            "transposition_non_immediate",
            "Les limites conceptuelles indiquent que les méthodes ne sont pas automatiquement transposables au contexte du dossier.",
        )

    if any(k in family_text for k in ["classification", "apprentissage", "profond", "deep", "evaluation", "évaluation"]):
        add_by_code(
            "robustesse_limitee",
            "Les familles identifiées impliquent des modèles de classification dont la robustesse doit être vérifiée.",
        )

    if any(k in text for k in ["protocole", "métrique", "metrique", "benchmark", "hyperparam", "parametre", "paramètre"]):
        add_by_code(
            "criteres_evaluation_insuffisants",
            "La comparaison des approches nécessite des critères d'évaluation, réglages ou protocoles adaptés au projet.",
        )

    out = []
    seen = set()

    for item in selected:
        code = item.get("code")
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(item)

    if len(out) < 3:
        fallback_codes = [
            "validation_incomplete",
            "transposition_non_immediate",
            "generalisation_limitee",
        ]

        for code in fallback_codes:
            if code in seen:
                continue

            for item in taxonomy:
                if item.get("code") == code:
                    enriched = dict(item)
                    enriched["selection_reason"] = "Insuffisance canonique CIR utilisée en fallback."
                    out.append(enriched)
                    seen.add(code)
                    break

    return out[:6]


# ============================================================
# Citation plan
# ============================================================

def citation_plan(verrou_gap: Dict[str, Any], supporting_articles: Optional[List[Dict[str, Any]]] = None) -> Dict[str, List[str]]:
    plan = verrou_gap.get("citation_plan_for_phase_5") or {}

    supporting_articles = supporting_articles or verrou_gap.get("supporting_articles") or []

    fallback_all = []
    fallback_main = []
    fallback_support = []
    fallback_methodological = []
    fallback_background = []

    for item in supporting_articles:
        if not isinstance(item, dict):
            continue

        citation = clean_text(item.get("citation_label")).strip("[]")
        if not citation:
            continue

        usage_type = normalize_usage_type(item.get("article_usage_type"))
        fallback_all.append(citation)

        if usage_type == "direct_evidence":
            fallback_main.append(citation)
        elif usage_type == "related_evidence":
            fallback_support.append(citation)
        elif usage_type == "methodological_evidence":
            fallback_methodological.append(citation)
        elif usage_type == "background_evidence":
            fallback_background.append(citation)
        else:
            fallback_background.append(citation)

    return {
        "main_citations": unique_clean_list(plan.get("main_citations", []) or fallback_main),
        "supporting_citations": unique_clean_list(plan.get("supporting_citations", []) or fallback_support),
        "methodological_citations": unique_clean_list(plan.get("methodological_citations", []) or fallback_methodological),
        "background_citations": unique_clean_list(plan.get("background_citations", []) or fallback_background),
        "all_allowed_citations": unique_clean_list(plan.get("all_allowed_citations", []) or fallback_all),
    }


def main_family_labels(families: List[Dict[str, Any]], max_items: int = 3) -> List[str]:
    labels = []

    for family in families:
        if family.get("direct_citations"):
            labels.append(clean_text(family.get("family_label")))

    if not labels:
        for family in families:
            labels.append(clean_text(family.get("family_label")))

    out = []
    seen = set()

    for label in labels:
        if not label or label in seen:
            continue

        seen.add(label)
        out.append(label)

        if len(out) >= max_items:
            break

    return out


def related_family_labels(families: List[Dict[str, Any]], max_items: int = 3) -> List[str]:
    labels = []

    for family in families:
        if family.get("related_citations"):
            labels.append(clean_text(family.get("family_label")))

    out = []
    seen = set()

    for label in labels:
        if not label or label in seen:
            continue

        seen.add(label)
        out.append(label)

        if len(out) >= max_items:
            break

    return out


def direct_citations_from_families(families: List[Dict[str, Any]]) -> List[str]:
    citations = []

    for family in families:
        citations.extend(family.get("direct_citations") or [])

    return unique_clean_list(citations)


def related_citations_from_families(families: List[Dict[str, Any]]) -> List[str]:
    citations = []

    for family in families:
        citations.extend(family.get("related_citations") or [])

    return unique_clean_list(citations)


def all_citations_from_families(families: List[Dict[str, Any]]) -> List[str]:
    citations = []

    for family in families:
        citations.extend(family.get("all_citations") or [])

    return unique_clean_list(citations)


def prioritized_citations_from_technical_methods(technical_methods: List[Dict[str, Any]]) -> List[str]:
    """
    Priorité réelle pour Phase 5 :
    - core d'abord
    - important ensuite
    - support ensuite
    - low_confidence toujours à la fin, même si l'article est tagué Direct
    """

    tier_priority = {
        "core": 0,
        "important": 1,
        "support": 2,
        "context_low_confidence": 3,
    }

    usage_priority = {
        "direct_evidence": 0,
        "related_evidence": 1,
        "methodological_evidence": 2,
        "background_evidence": 3,
    }

    sorted_methods = sorted(
        technical_methods,
        key=lambda x: (
            tier_priority.get(x.get("priority_tier"), 9),
            usage_priority.get(normalize_usage_type(x.get("usage_type")), 9),
            -safe_float(x.get("priority_score"), 0.0),
            x.get("citation_label") or "",
        )
    )

    return unique_clean_list([x.get("citation_label") for x in sorted_methods])


def citations_by_tier(technical_methods: List[Dict[str, Any]], tier: str) -> List[str]:
    return unique_clean_list([
        x.get("citation_label")
        for x in technical_methods
        if x.get("priority_tier") == tier
    ])


# ============================================================
# Safe source gap summary
# ============================================================

def build_safe_source_gap_summary(
    verrou_gap: Dict[str, Any],
    families: List[Dict[str, Any]],
    technical_methods: List[Dict[str, Any]],
) -> Dict[str, Any]:
    verrou_title = clean_text(verrou_gap.get("verrou_title"))
    direct_count = sum(len(f.get("direct_citations") or []) for f in families)
    related_count = sum(len(f.get("related_citations") or []) for f in families)

    family_labels = main_family_labels(families, max_items=4)
    family_txt = ", ".join(family_labels) if family_labels else "plusieurs approches scientifiques"

    core_methods = [
        clean_text(x.get("method_name") or x.get("technical_family"))
        for x in technical_methods
        if x.get("priority_tier") in {"core", "important"}
        and clean_text(x.get("method_name") or x.get("technical_family"))
    ]

    core_methods = unique_clean_list(core_methods)[:5]
    methods_txt = ", ".join(core_methods) if core_methods else "des concepts et méthodes identifiés dans la littérature"

    return {
        "summary_type": "safe_gap_summary_for_phase_5",
        "verrou_title": verrou_title,
        "scientific_gap_summary": (
            f"Le gap scientifique porte sur l'écart entre les approches identifiées dans l'état de l'art "
            f"et leur validation effective dans le contexte du projet. Les familles principales à discuter "
            f"sont : {family_txt}. Les concepts ou méthodes à expliquer techniquement incluent : {methods_txt}. "
            f"La difficulté centrale est de démontrer que ces approches permettent réellement de répondre aux "
            f"exigences de performance, robustesse, généralisation et représentativité attendues."
        ),
        "rd_justification_summary": (
            "Des travaux R&D spécifiques restent nécessaires afin de tester, adapter et valider les approches "
            "identifiées dans un protocole propre au dossier courant. La justification doit s'appuyer sur les "
            "limites des concepts existants et sur leur non-transposabilité directe."
        ),
        "evidence_balance": {
            "direct_citations_count": direct_count,
            "related_citations_count": related_count,
            "all_citations_count": len(all_citations_from_families(families)),
            "direct_citations": direct_citations_from_families(families),
            "related_citations": related_citations_from_families(families),
            "all_allowed_citations": all_citations_from_families(families),
            "no_article_ignored": True,
        },
        "phase_5_instruction": (
            "Utiliser ce résumé sûr plutôt que le scientific_gap brut de Phase 4. "
            "Expliquer les méthodes/concepts, leurs mécanismes et leurs limites. "
            "Ne pas reprendre les formulations du type 'les articles A1, A2'."
        ),
    }


# ============================================================
# Reasoning sections
# ============================================================

def build_methods_text_for_reasoning(technical_methods: List[Dict[str, Any]], max_items: int = 0) -> str:
    """
    Construit le bloc des méthodes/concepts pour Phase 5.

    Correction ciblée :
    - max_items <= 0 signifie : ne pas limiter.
    - Avant, les appels max_items=6 ou max_items=10 pouvaient cacher des articles
      pourtant sélectionnés dans le verrou.
    """
    lines = []

    selected_methods = (
        technical_methods
        if max_items is None or max_items <= 0
        else technical_methods[:max_items]
    )

    for item in selected_methods:
        citation = item.get("citation_label")
        name = clean_text(item.get("method_name") or item.get("technical_family") or item.get("concept_label") or "concept non nommé")
        principle = truncate(item.get("technical_principle"), 280)
        mechanism = truncate(item.get("mechanism"), 280)
        dataset = truncate(item.get("dataset_context"), 220)
        validation = truncate(item.get("validation_protocol"), 240)
        results = truncate(item.get("reported_results"), 220)
        limits = "; ".join(truncate(x, 200) for x in (item.get("concept_limits") or [])[:2])
        trans = "; ".join(truncate(x, 200) for x in (item.get("transposability_limits") or [])[:2])
        tier = item.get("priority_tier")
        usage = item.get("usage_type")

        parts = [
            f"[{citation}] {name}",
            f"usage={usage}",
            f"priorité={tier}",
        ]

        if principle:
            parts.append(f"principe={principle}")

        if mechanism:
            parts.append(f"mécanisme={mechanism}")

        if dataset:
            parts.append(f"données={dataset}")

        if validation:
            parts.append(f"validation={validation}")

        if results:
            parts.append(f"résultats={results}")

        if limits:
            parts.append(f"limites conceptuelles={limits}")

        if trans:
            parts.append(f"limites de transposition={trans}")

        detail_summary = summarize_technical_detail_profile(item.get("technical_detail_profile") or {}, max_chars=650)
        if detail_summary:
            parts.append(f"détails techniques explicites={detail_summary}")
        elif item.get("missing_technical_details"):
            parts.append("détails techniques manquants=" + "; ".join(item.get("missing_technical_details")[:3]))

        lines.append("- " + " | ".join(parts))

    return "\n".join(lines)

def build_reasoning_sections(
    *,
    verrou_gap: Dict[str, Any],
    families: List[Dict[str, Any]],
    technical_methods: List[Dict[str, Any]],
    insufficiencies: List[Dict[str, Any]],
    argumentation_profile: Dict[str, Any],
) -> Dict[str, Any]:
    verrou_title = clean_text(verrou_gap.get("verrou_title"))
    objectif_rd = clean_text(verrou_gap.get("objectif_rd") or verrou_title)

    plan = citation_plan(verrou_gap)

    main_citations = plan["main_citations"] or direct_citations_from_families(families)
    supporting_citations = plan["supporting_citations"] or related_citations_from_families(families)

    direct_labels = main_family_labels(families)
    related_labels = related_family_labels(families)

    direct_families_txt = (
        ", ".join(direct_labels)
        if direct_labels
        else "plusieurs familles d'approches directement liées"
    )

    related_families_txt = (
        ", ".join(related_labels)
        if related_labels
        else "des approches méthodologiques connexes"
    )

    insuff_labels = [
        clean_text(x.get("label"))
        for x in insufficiencies
        if clean_text(x.get("label"))
    ]
    insuff_txt = (
        ", ".join(insuff_labels[:5])
        if insuff_labels
        else "des limites de validation et de transposition"
    )

    main_refs = format_citation_list(main_citations)
    supporting_refs = format_citation_list(supporting_citations)

    core_methods = [
        x for x in technical_methods
        if x.get("usage_type") == "direct_evidence"
    ]

    if not core_methods:
        core_methods = technical_methods

    methods_reasoning_text = build_methods_text_for_reasoning(core_methods, max_items=0)
    all_methods_reasoning_text = build_methods_text_for_reasoning(technical_methods, max_items=0)

    section_blueprints = argumentation_profile.get("section_blueprints") or {}

    sections = {
        "positionnement_scientifique_du_verrou": {
            "goal": "Situer le verrou sans commencer par les articles.",
            "reasoning": (
                f"Le verrou porte sur « {verrou_title} ». La difficulté scientifique ne consiste pas seulement "
                f"à appliquer une méthode connue, mais à vérifier que les approches disponibles permettent de "
                f"répondre à l'objectif R&D suivant : {objectif_rd}. Le raisonnement doit donc partir de la "
                f"validation attendue dans le contexte du projet, puis expliquer pourquoi l'état de l'art ne permet "
                f"pas de conclure directement."
            ),
            "citation_strategy": "Pas de citation obligatoire dans cette section si elle pose uniquement le verrou.",
            "blueprint": section_blueprints.get("positionnement_scientifique_du_verrou", {}),
        },
        "travaux_existants_directement_lies": {
            "goal": "Présenter les preuves directes par concepts/méthodes et familles d'approches.",
            "reasoning": (
                f"Les travaux directement liés doivent être regroupés autour de familles d'approches, notamment : "
                f"{direct_families_txt}. Contrairement à une synthèse générale, Phase 5 doit expliquer les concepts "
                f"techniques eux-mêmes : nom de méthode si disponible, principe, mécanisme, apport, limite du concept. "
                f"Les citations Direct doivent rester en fin de phrase ou de paragraphe : {main_refs}.\n\n"
                f"Méthodes/concepts directs à expliquer techniquement :\n{methods_reasoning_text}"
            ),
            "citation_strategy": f"Utiliser prioritairement les citations Direct : {main_refs}. Les noms de méthodes sont autorisés, mais pas la formule 'l'article [A] présente'.",
            "blueprint": section_blueprints.get("travaux_existants_directement_lies", {}),
        },
        "travaux_connexes_ou_methodes_transposables": {
            "goal": "Utiliser les articles connexes comme éclairage méthodologique, sans les ignorer.",
            "reasoning": (
                f"Les travaux connexes doivent être conservés car ils ont été sélectionnés, mais ils doivent être "
                f"pondérés selon leur proximité au verrou. Ils servent à éclairer {related_families_txt}, la robustesse, "
                f"les principes de classification, la segmentation, l'évaluation ou la transposition. Il ne faut pas "
                f"les transformer en preuves directes. Citations Connexes : {supporting_refs}.\n\n"
                f"Éléments techniques conservés avec priorité pondérée :\n{all_methods_reasoning_text}"
            ),
            "citation_strategy": f"Utiliser les citations Connexes {supporting_refs} uniquement en appui connexe ou transposabilité.",
            "blueprint": section_blueprints.get("travaux_connexes_ou_methodes_transposables", {}),
        },
        "limites_de_l_etat_de_l_art_au_regard_du_projet": {
            "goal": "Formuler les insuffisances persistantes à partir des limites des concepts.",
            "reasoning": (
                f"Les limites de l'état de l'art doivent être formulées autour des insuffisances suivantes : "
                f"{insuff_txt}. Le texte doit montrer les limites des concepts existants : dépendance aux données, "
                f"réglage des paramètres, validité locale, hypothèses de modèle, protocole de validation, généralisation "
                f"ou représentativité. Le verrou doit être mis en évidence à travers ces limites : ce que les concepts "
                f"ne garantissent pas encore dans le contexte du projet."
            ),
            "citation_strategy": (
                "Les citations doivent soutenir les limites, mais sans devenir le sujet grammatical des phrases."
            ),
            "blueprint": section_blueprints.get("limites_de_l_etat_de_l_art", {}),
        },
        "gap_scientifique_technique_justifiant_les_travaux_rd": {
            "goal": "Exprimer l'écart entre concepts existants et besoin projet.",
            "reasoning": (
                f"Le gap scientifique réside dans l'écart entre les concepts/méthodes identifiés et leur validation "
                f"dans le contexte spécifique du projet. Même si les travaux sélectionnés fournissent des pistes "
                f"pertinentes, ils ne démontrent pas automatiquement la faisabilité, la robustesse, la généralisation "
                f"ou la représentativité attendue pour lever le verrou « {verrou_title} ». Le gap doit être formulé "
                f"comme une incertitude technique résiduelle, pas comme une simple absence d'article."
            ),
            "citation_strategy": (
                f"S'appuyer principalement sur les citations Direct {main_refs}. "
                f"Les citations Connexes {supporting_refs} peuvent être utilisées seulement pour discuter la transposition."
            ),
            "blueprint": section_blueprints.get("gap_scientifique_technique", {}),
        },
        "synthese_cir_exploitable": {
            "goal": "Conclure sur la nécessité de travaux R&D.",
            "reasoning": (
                f"Le raisonnement doit conclure que des travaux R&D spécifiques restent nécessaires pour tester, "
                f"adapter et valider les approches identifiées. La synthèse doit insister sur la nécessité d'un "
                f"protocole propre au dossier, permettant de réduire les incertitudes relatives à l'efficacité, "
                f"la robustesse, la représentativité des données et la transposition des méthodes dans le contexte du projet."
            ),
            "citation_strategy": "Citations facultatives si la section synthétise le gap déjà cité.",
            "blueprint": section_blueprints.get("synthese_cir_exploitable", {}),
        },
    }

    return sections


# ============================================================
# Payload pour Phase 5
# ============================================================

def build_families_summary_for_phase_5(families: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []

    for family in families:
        direct = family.get("direct_citations") or []
        related = family.get("related_citations") or []
        methodological = family.get("methodological_citations") or []
        background = family.get("background_citations") or []

        out.append({
            "family_id": family.get("family_id"),
            "family_label": family.get("family_label"),
            "evidence_strength": family.get("evidence_strength"),

            "direct_citations": direct,
            "direct_citation_text": format_citation_list(direct),

            "related_citations": related,
            "related_citation_text": format_citation_list(related),

            "methodological_citations": methodological,
            "methodological_citation_text": format_citation_list(methodological),

            "background_citations": background,
            "background_citation_text": format_citation_list(background),

            "all_citations_for_traceability_only": family.get("all_citations") or [],
            "weighted_citations": family.get("weighted_citations") or [],

            "signals": family.get("signals") or [],

            "technical_methods": family.get("technical_methods") or [],

            "writer_instruction": (
                "Présenter cette famille comme un groupe d'approches ou de concepts. "
                "Expliquer techniquement les méthodes importantes : principe, mécanisme, limites conceptuelles, "
                "impact sur le verrou. "
                "Utiliser direct_citations comme preuves centrales. "
                "Utiliser related_citations uniquement comme appuis méthodologiques ou discussion de transposabilité. "
                "Ne jamais fusionner une citation connexe comme preuve directe. "
                "Ne pas ignorer les articles sélectionnés, mais respecter leur poids."
            ),
        })

    return out


def build_insufficiencies_summary_for_phase_5(insufficiencies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []

    for item in insufficiencies:
        out.append({
            "code": item.get("code"),
            "label": item.get("label"),
            "template": item.get("template"),
            "selection_reason": item.get("selection_reason"),
            "writer_instruction": (
                "Utiliser cette insuffisance pour construire le gap scientifique, "
                "en la reliant aux limites des concepts et méthodes existants."
            ),
        })

    return out


def build_phase_5_reasoning_block(
    *,
    verrou_gap: Dict[str, Any],
    families: List[Dict[str, Any]],
    technical_methods: List[Dict[str, Any]],
    insufficiencies: List[Dict[str, Any]],
    reasoning_sections: Dict[str, Any],
    argumentation_profile: Dict[str, Any],
    consultant_reasoning_enrichment: Optional[Dict[str, Any]] = None,
) -> str:
    lines = []

    lines.append("SCIENTIFIC REASONING BUILDER — CONTEXTE POUR PHASE 5")
    lines.append(
        "Ce bloc contient le raisonnement scientifique à suivre. "
        "Il ne remplace pas les Article Cards et ne doit pas être cité comme source."
    )

    lines.append("\nVERROU")
    lines.append(clean_text(verrou_gap.get("verrou_title")))

    lines.append("\nLOGIQUE CONSULTANT")
    writer_logic = argumentation_profile.get("writer_logic") or {}
    if writer_logic.get("main_principle"):
        lines.append(f"- {writer_logic.get('main_principle')}")
    if writer_logic.get("article_usage"):
        lines.append(f"- {writer_logic.get('article_usage')}")
    if writer_logic.get("related_articles_usage"):
        lines.append(f"- {writer_logic.get('related_articles_usage')}")

    lines.append("\nFAMILLES D'APPROCHES À SYNTHÉTISER")
    for family in families:
        direct_txt = format_citation_list(family.get("direct_citations") or [])
        related_txt = format_citation_list(family.get("related_citations") or [])

        line = f"- {family.get('family_label')} : "

        parts = []
        if direct_txt:
            parts.append(f"preuves Direct {direct_txt}")
        if related_txt:
            parts.append(f"appuis Connexes uniquement {related_txt}")

        if not parts:
            parts.append("contexte méthodologique sans preuve centrale")

        line += " ; ".join(parts)
        line += ". Ne pas fusionner les connexes comme preuves directes."
        lines.append(line)

    lines.append("\nMÉTHODES / CONCEPTS TECHNIQUES À EXPLIQUER")
    for tm in technical_methods:
        citation = tm.get("citation_label")
        name = tm.get("method_name") or tm.get("technical_family") or "concept non nommé"
        tier = tm.get("priority_tier")
        usage = tm.get("usage_type")

        lines.append(f"\n- [{citation}] {name} | usage={usage} | priorité={tier}")
        if tm.get("technical_principle"):
            lines.append(f"  Principe : {tm.get('technical_principle')}")
        if tm.get("mechanism"):
            lines.append(f"  Mécanisme : {tm.get('mechanism')}")
        if tm.get("concept_limits"):
            lines.append("  Limites du concept : " + " ; ".join(tm.get("concept_limits")[:3]))
        if tm.get("transposability_limits"):
            lines.append("  Limites de transposition : " + " ; ".join(tm.get("transposability_limits")[:3]))
        if tm.get("impact_on_verrou"):
            lines.append(f"  Lien avec le verrou : {tm.get('impact_on_verrou')}")

    lines.append("\nINSUFFISANCES À FAIRE APPARAÎTRE")
    for item in insufficiencies:
        lines.append(f"- {item.get('label')} : {item.get('selection_reason')}")

    lines.append("\nPLAN DE RAISONNEMENT PAR SECTION")
    for key, section in reasoning_sections.items():
        lines.append(f"\n[{key}]")
        lines.append(section.get("reasoning") or "")

    enrichment_text = format_consultant_enrichment_for_prompt(consultant_reasoning_enrichment or {})
    if enrichment_text:
        lines.append("\n" + enrichment_text)

    lines.append("\nRÈGLE MAJEURE")
    lines.append(
        "Le texte final doit raisonner à partir du verrou, des concepts, des méthodes et de leurs limites. "
        "Les articles servent d'appui scientifique en fin de phrase. "
        "Les noms de méthodes sont autorisés, mais il ne faut pas écrire 'l'article [A1] présente'. "
        "Les citations Connexes ne doivent jamais être présentées comme preuves directes. "
        "Aucun article sélectionné par le consultant ne doit être supprimé ; les moins proches sont simplement pondérés plus faiblement."
    )

    return "\n".join(lines).strip()


def build_writer_plan_for_phase_5(
    *,
    verrou_gap: Dict[str, Any],
    families: List[Dict[str, Any]],
    technical_methods: List[Dict[str, Any]],
    insufficiencies: List[Dict[str, Any]],
    reasoning_sections: Dict[str, Any],
    consultant_reasoning_enrichment: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    plan = citation_plan(verrou_gap)

    direct_from_families = direct_citations_from_families(families)
    related_from_families = related_citations_from_families(families)
    all_from_families = all_citations_from_families(families)

    main_citations = plan["main_citations"] or direct_from_families
    supporting_citations = plan["supporting_citations"] or related_from_families
    allowed_citations = unique_clean_list(plan["all_allowed_citations"] or all_from_families)

    # Important : ne jamais perdre une citation sélectionnée.
    for citation in all_from_families:
        if citation not in allowed_citations:
            allowed_citations.append(citation)

    prioritized = prioritized_citations_from_technical_methods(technical_methods)

    core = citations_by_tier(technical_methods, "core")
    important = citations_by_tier(technical_methods, "important")
    support = citations_by_tier(technical_methods, "support")
    low_confidence = citations_by_tier(technical_methods, "context_low_confidence")

    return {
        "writer_objective": (
            "Rédiger un état de l'art CIR consultant à partir du raisonnement scientifique, "
            "en expliquant les concepts/méthodes, leurs mécanismes, leurs limites et le verrou restant."
        ),
        "allowed_citations": allowed_citations,
        "coverage_required_citations": allowed_citations,
        "mandatory_coverage_rule": {
            "must_cover_all_allowed_citations": True,
            "scope": "per_verrou",
            "instruction": (
                "Phase 5 doit couvrir toutes les citations autorisées du verrou. "
                "Les Direct sont des preuves principales, les Connexes des appuis, "
                "les Méthodologiques/Fondamentaux servent au cadrage. La pondération organise l'argumentation, "
                "mais ne doit pas supprimer un article sélectionné."
            ),
        },
        "main_citations": main_citations,
        "supporting_citations": supporting_citations,
        "direct_citations_from_families": direct_from_families,
        "related_citations_from_families": related_from_families,
        "all_citations_from_families": all_from_families,
        "prioritized_citations": prioritized,
        "core_citations": core,
        "important_citations": important,
        "support_citations": support,
        "low_confidence_citations": low_confidence,
        "no_article_ignored": True,
        "section_order": list(reasoning_sections.keys()),
        "families_to_synthesize": build_families_summary_for_phase_5(families),
        "technical_methods_to_explain": technical_methods,
        "concept_limit_matrix": build_concept_limit_matrix(technical_methods=technical_methods),
        "technical_detail_matrix": build_technical_detail_matrix(technical_methods),
        "insufficiencies_to_discuss": build_insufficiencies_summary_for_phase_5(insufficiencies),
        "consultant_reasoning_enrichment": consultant_reasoning_enrichment or {},
        "reasoning_v1_3_required_moves": [
            "Construire une histoire scientifique avant de citer les travaux.",
            "Regrouper les articles en familles d'idées et éviter le résumé article-par-article.",
            "Approfondir les concepts uniquement si les Article Cards contiennent principe, mécanisme ou limite.",
            "Comparer seulement si comparison_guard_matrix autorise la comparaison.",
            "Transformer les limites en cause -> conséquence -> impact projet.",
            "Construire la conclusion comme une démonstration progressive, pas comme une affirmation.",
        ],
        "style_constraints": [
            "Commencer par le verrou, pas par les articles.",
            "Regrouper les travaux par familles d'approches.",
            "Expliquer les méthodes/concepts importants avec leur nom si disponible.",
            "Pour chaque concept important : principe, mécanisme, apport, limite du concept, lien au verrou.",
            "Employer des connecteurs CIR : Malgré, Toutefois, Cependant, Ainsi.",
            "Ne pas ignorer les articles sélectionnés par le consultant.",
            "Pondérer les articles : les plus proches du verrou doivent être au centre, les plus éloignés restent en appui.",
            "Les articles sans fulltext peuvent être conservés, mais doivent être utilisés avec prudence.",
            "Placer les citations uniquement en fin de phrase ou de paragraphe.",
            "Ne jamais écrire : l'article [A1] présente.",
            "Ne jamais écrire : les articles [A1], [A2] montrent.",
            "Ne jamais utiliser une citation comme sujet grammatical.",
            "Ne jamais mélanger les citations Connexes avec les preuves Directes.",
            "Les citations Connexes servent uniquement à discuter la transposabilité ou la méthode.",
        ],
        "expected_output_style": (
            "Texte argumentatif de consultant CIR : concept technique -> mécanisme -> limite du concept -> verrou restant -> R&D."
        ),
    }


# ============================================================
# Quality
# ============================================================

def validate_reasoning_item(item: Dict[str, Any]) -> Dict[str, Any]:
    warnings = []
    score = 100

    families = item.get("approach_families") or []
    insufficiencies = item.get("selected_insufficiencies") or []
    reasoning_sections = item.get("reasoning_sections") or {}
    writer_plan = item.get("writer_plan_for_phase_5") or {}
    technical_methods = item.get("technical_methods_reasoning") or []
    enrichment = item.get("consultant_reasoning_enrichment") or {}

    if not enrichment:
        score -= 10
        warnings.append("Enrichissement consultant V1.3 absent.")

    if enrichment and not (enrichment.get("scientific_storyline") or {}):
        score -= 5
        warnings.append("Storyline scientifique absente.")

    if enrichment and not (enrichment.get("technical_limit_causality") or []):
        score -= 5
        warnings.append("Limites causales absentes.")

    if families and not (enrichment.get("comparison_guard_matrix") or []):
        warnings.append("Aucune comparaison contrôlée produite ; Phase 5 devra seulement regrouper sans comparer.")

    if not families:
        score -= 30
        warnings.append("Aucune famille d'approches construite.")

    if not technical_methods:
        score -= 25
        warnings.append("Aucun raisonnement technique par méthode/concept.")

    if not insufficiencies:
        score -= 25
        warnings.append("Aucune insuffisance sélectionnée.")

    if len(reasoning_sections) < 6:
        score -= 20
        warnings.append("Sections de raisonnement incomplètes.")

    if not writer_plan.get("allowed_citations"):
        score -= 20
        warnings.append("Aucune citation autorisée transmise à Phase 5.")

    if not writer_plan.get("direct_citations_from_families"):
        score -= 10
        warnings.append("Aucune citation Direct séparée dans writer_plan_for_phase_5.")

    if writer_plan.get("no_article_ignored") is not True:
        score -= 20
        warnings.append("La règle no_article_ignored n'est pas activée.")

    # Vérifier que toutes les citations des familles sont dans allowed.
    all_family_citations = set(writer_plan.get("all_citations_from_families") or [])
    allowed = set(writer_plan.get("allowed_citations") or [])
    missing_allowed = sorted(all_family_citations - allowed)

    if missing_allowed:
        score -= 30
        warnings.append("Certaines citations sélectionnées ne sont pas dans allowed_citations : " + ", ".join(missing_allowed))

    weak_articles = [
        x.get("citation_label")
        for x in technical_methods
        if x.get("priority_tier") == "context_low_confidence"
    ]

    if weak_articles:
        warnings.append(
            "Articles conservés avec faible confiance, à utiliser prudemment : "
            + ", ".join(unique_clean_list(weak_articles))
        )

    if item.get("rules", {}).get("memory_as_proof") is not False:
        score -= 40
        warnings.append("Règle memory_as_proof incorrecte.")

    score = max(0, min(100, score))

    if score >= 85:
        level = "good"
    elif score >= 60:
        level = "usable"
    else:
        level = "weak"

    return {
        "score": score,
        "level": level,
        "warnings": warnings,
    }


def validate_payload(reasoning_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not reasoning_items:
        return {
            "score": 0,
            "level": "weak",
            "warnings": ["Aucun raisonnement par verrou produit."],
        }

    scores = []
    warnings = []

    for item in reasoning_items:
        q = item.get("quality") or {}
        scores.append(int(q.get("score") or 0))
        warnings.extend(q.get("warnings") or [])

    avg_score = round(sum(scores) / len(scores), 2) if scores else 0

    if avg_score >= 85:
        level = "good"
    elif avg_score >= 60:
        level = "usable"
    else:
        level = "weak"

    return {
        "score": avg_score,
        "level": level,
        "warnings": warnings,
        "verrous_count": len(reasoning_items),
    }


# ============================================================
# Main API
# ============================================================

def build_scientific_reasoning_payload(
    organisme: str,
    project: str,
    year: str,
    gap_payload_path: Optional[str | Path] = None,
    article_cards_payload_path: Optional[str | Path] = None,
    argumentation_payload_path: Optional[str | Path] = None,
    output_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """
    Construit le payload Phase 4.5.

    Entrées :
    - Phase 4 gap_scientific_payload.json ;
    - Article Cards Phase 2 enrichies ;
    - Phase 3.5 argumentation_profile_payload.json.
    """

    gap_path = (
        Path(gap_payload_path)
        if gap_payload_path
        else default_gap_payload_path(organisme, project, year)
    )

    cards_path = (
        Path(article_cards_payload_path)
        if article_cards_payload_path
        else default_article_cards_payload_path(organisme, project, year)
    )

    argumentation_path = (
        Path(argumentation_payload_path)
        if argumentation_payload_path
        else default_argumentation_payload_path(organisme, project, year)
    )

    out_path = (
        Path(output_path)
        if output_path
        else scientific_reasoning_output_path(organisme, project, year)
    )

    gap_payload = _read_json(gap_path, {}) or {}
    article_cards_payload = _read_json(cards_path, {}) or {}
    argumentation_payload = _read_json(argumentation_path, {}) or {}

    if not gap_payload:
        result = {
            "ok": False,
            "phase": "phase_4_5_scientific_reasoning_builder",
            "status": "missing_gap_payload",
            "message": "gap_scientific_payload.json introuvable ou vide.",
            "input_paths": {
                "gap_payload": str(gap_path),
                "article_cards_payload": str(cards_path),
                "argumentation_payload": str(argumentation_path),
            },
            "output_path": str(out_path),
        }
        _write_json(out_path, result)
        return result

    if not article_cards_payload:
        result = {
            "ok": False,
            "phase": "phase_4_5_scientific_reasoning_builder",
            "status": "missing_article_cards_payload",
            "message": "article_cards_payload.json introuvable ou vide.",
            "input_paths": {
                "gap_payload": str(gap_path),
                "article_cards_payload": str(cards_path),
                "argumentation_payload": str(argumentation_path),
            },
            "output_path": str(out_path),
        }
        _write_json(out_path, result)
        return result

    if not argumentation_payload:
        result = {
            "ok": False,
            "phase": "phase_4_5_scientific_reasoning_builder",
            "status": "missing_argumentation_payload",
            "message": "argumentation_profile_payload.json introuvable ou vide. Relance Phase 3 avec run_argumentation_profile=True.",
            "input_paths": {
                "gap_payload": str(gap_path),
                "article_cards_payload": str(cards_path),
                "argumentation_payload": str(argumentation_path),
            },
            "output_path": str(out_path),
        }
        _write_json(out_path, result)
        return result

    article_cards = extract_article_cards(article_cards_payload)
    argumentation_profile = get_argumentation_profile(argumentation_payload)
    verrous_gap = gap_payload.get("verrous_gap_analysis") or []
    project_context = gap_payload.get("project_context") or {}

    reasoning_items = []

    for verrou_gap in verrous_gap:
        if not isinstance(verrou_gap, dict):
            continue

        families = build_families_from_articles(
            verrou_gap=verrou_gap,
            article_cards=article_cards,
        )

        technical_methods = build_technical_methods_reasoning(
            verrou_gap=verrou_gap,
            families=families,
        )

        concept_limit_matrix = build_concept_limit_matrix(
            technical_methods=technical_methods,
        )

        technical_detail_matrix = build_technical_detail_matrix(technical_methods)

        insufficiencies = choose_insufficiencies_for_verrou(
            verrou_gap=verrou_gap,
            families=families,
            technical_methods=technical_methods,
            argumentation_profile=argumentation_profile,
        )

        consultant_reasoning_enrichment = build_consultant_reasoning_enrichment(
            verrou_gap=verrou_gap,
            families=families,
            technical_methods=technical_methods,
            insufficiencies=insufficiencies,
        )

        reasoning_sections = build_reasoning_sections(
            verrou_gap=verrou_gap,
            families=families,
            technical_methods=technical_methods,
            insufficiencies=insufficiencies,
            argumentation_profile=argumentation_profile,
        )

        reasoning_block = build_phase_5_reasoning_block(
            verrou_gap=verrou_gap,
            families=families,
            technical_methods=technical_methods,
            insufficiencies=insufficiencies,
            reasoning_sections=reasoning_sections,
            argumentation_profile=argumentation_profile,
            consultant_reasoning_enrichment=consultant_reasoning_enrichment,
        )

        writer_plan = build_writer_plan_for_phase_5(
            verrou_gap=verrou_gap,
            families=families,
            technical_methods=technical_methods,
            insufficiencies=insufficiencies,
            reasoning_sections=reasoning_sections,
            consultant_reasoning_enrichment=consultant_reasoning_enrichment,
        )

        safe_gap_summary = build_safe_source_gap_summary(
            verrou_gap=verrou_gap,
            families=families,
            technical_methods=technical_methods,
        )

        item = {
            "verrou_id": clean_text(verrou_gap.get("verrou_id")),
            "verrou_index": verrou_gap.get("verrou_index"),
            "verrou_title": clean_text(verrou_gap.get("verrou_title")),
            "objectif_rd": clean_text(verrou_gap.get("objectif_rd")),

            "approach_families": families,

            # Nouveau bloc central pour Phase 5.
            "technical_methods_reasoning": technical_methods,
            "concept_limit_matrix": concept_limit_matrix,
            "technical_detail_matrix": technical_detail_matrix,
            "consultant_reasoning_enrichment": consultant_reasoning_enrichment,

            "selected_insufficiencies": insufficiencies,
            "reasoning_sections": reasoning_sections,
            "reasoning_block_for_phase_5": reasoning_block,
            "writer_plan_for_phase_5": writer_plan,

            # Champ sûr pour Phase 5.
            "source_gap_summary_for_phase_5": safe_gap_summary,

            # Champ conservé uniquement pour audit / traçabilité.
            # Phase 5 ne doit pas injecter ce contenu brut dans le prompt.
            "source_gap_traceability_not_for_phase_5": {
                "scientific_gap_raw": verrou_gap.get("scientific_gap"),
                "rd_justification_raw": verrou_gap.get("rd_justification"),
                "non_transposability_raw": verrou_gap.get("non_transposability") or [],
                "article_limitations_raw": verrou_gap.get("article_limitations") or [],
                "confidence": verrou_gap.get("confidence") or {},
                "risk_level": verrou_gap.get("risk_level"),
                "usage": "traceability_only",
                "do_not_inject_in_llm_prompt": True,
            },

            "rules": {
                "usage": "scientific_reasoning_only",
                "article_cards_as_only_scientific_proof": True,
                "argumentation_profile_as_proof": False,
                "memory_as_proof": False,
                "can_be_cited": False,
                "citations_allowed_only_from_article_cards_and_phase_4_plan": True,
                "writer_must_not_write_article_by_article": True,
                "method_names_are_allowed": True,
                "explain_concepts_not_articles": True,
                "direct_and_related_citations_separated": True,
                "source_gap_raw_not_for_phase_5": True,
                "no_consultant_selected_article_ignored": True,
                "weighted_evidence_not_hard_filtering": True,
                "domain_specific_hardcoding": False,
                "consultant_reasoning_enrichment_v1_3": True,
                "comparison_guard_enabled": True,
                "causal_limit_reasoning_enabled": True,
                "scientific_storyline_enabled": True,
            "article_evidence_bank_priority": True,
            },
        }

        item["quality"] = validate_reasoning_item(item)
        reasoning_items.append(item)

    quality = validate_payload(reasoning_items)

    result = {
        "ok": bool(reasoning_items) and quality.get("level") in {"good", "usable"},
        "phase": "phase_4_5_scientific_reasoning_builder",
        "step": "build_scientific_reasoning_payload",
        "payload_type": "scientific_reasoning_payload_v2_1_open_domain_no_loss_technical_extraction",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "organisme": organisme,
        "project": project,
        "year": str(year),
        "input_paths": {
            "gap_payload": str(gap_path),
            "article_cards_payload": str(cards_path),
            "argumentation_payload": str(argumentation_path),
        },
        "project_context": project_context,
        "argumentation_profile_summary": {
            "profile_type": argumentation_profile.get("profile_type"),
            "profile_strategy": argumentation_profile.get("profile_strategy"),
            "quality": argumentation_profile.get("quality"),
            "writer_logic": argumentation_profile.get("writer_logic"),
        },
        "verrous_reasoning": reasoning_items,
        "quality": quality,
        "rules": {
            "usage": "scientific_reasoning_only",
            "article_cards_as_only_scientific_proof": True,
            "argumentation_profile_as_proof": False,
            "memory_as_proof": False,
            "can_be_cited": False,
            "scientific_sources_allowed": "article_cards_only",
            "phase_5_should_use_this_payload_before_llm": True,
            "raw_memory_sentences_injected": False,
            "historical_facts_copied": False,
            "direct_and_related_citations_separated": True,
            "source_gap_raw_not_for_phase_5": True,
            "technical_method_analysis_consumed": True,
            "phase2_evidence_bank_consumed": True,
            "explicit_technical_details_extracted": True,
            "no_technical_value_invented": True,
            "extractive_paragraphs_consumed": True,
            "concept_limits_consumed": True,
            "impact_on_verrou_consumed": True,
            "weighted_evidence_enabled": True,
            "no_consultant_selected_article_ignored": True,
            "domain_specific_hardcoding": False,
            "consultant_reasoning_enrichment_v1_3": True,
            "comparison_guard_enabled": True,
            "causal_limit_reasoning_enabled": True,
            "scientific_storyline_enabled": True,
            "article_evidence_bank_priority": True,
            "explicit_technical_details_extracted": True,
            "no_technical_value_invented": True,
        },
        "output_path": str(out_path),
    }

    _write_json(out_path, result)
    return result

# ============================================================
# Alias compatibilité backend_api
# ============================================================

def run_phase_4_5_scientific_reasoning(
    organisme: str,
    project: str,
    year: str,
    gap_payload_path=None,
    article_cards_payload_path=None,
    argumentation_payload_path=None,
    output_path=None,
    *args,
    **kwargs,
):
    """
    Alias attendu par le backend.

    Le fichier Phase 4.5 possède déjà la vraie fonction :
        build_scientific_reasoning_payload(...)

    Cet alias évite l'erreur :
        cannot import name 'run_phase_4_5_scientific_reasoning'
    """
    return build_scientific_reasoning_payload(
        organisme=organisme,
        project=project,
        year=year,
        gap_payload_path=gap_payload_path,
        article_cards_payload_path=article_cards_payload_path,
        argumentation_payload_path=argumentation_payload_path,
        output_path=output_path,
    )


# Alias court optionnel
run_phase_4_5 = run_phase_4_5_scientific_reasoning


# ============================================================
# V2.0 — Multi-domain scientific method & technical detail extraction
# ============================================================
# Cette couche surcharge la V1.9 sans hardcoding projet/citation.
# Objectif : extraire les méthodes, paramètres, métriques et conditions
# pour tout type de domaine scientifique/technique : IA, mécanique,
# chimie, matériaux, bâtiment, simulation, expérimentation, instrumentation.

MULTIDOMAIN_TECH_MAX_SOURCE_CHARS = 26000


def _v20_findall_unique(text: str, patterns: List[str], *, max_items: int = 18) -> List[str]:
    values: List[str] = []
    seen = set()
    for pat in patterns:
        try:
            for m in re.finditer(pat, text, flags=re.IGNORECASE | re.MULTILINE):
                value = clean_text(m.group(0))
                # éviter les fragments purement bibliographiques trop longs
                value = truncate(value, 360)
                key = normalize_for_match(value)[:180]
                if not value or key in seen or is_placeholder_text(value):
                    continue
                seen.add(key)
                values.append(value)
                if len(values) >= max_items:
                    return values
        except re.error:
            continue
    return values


def _v20_snippets(text: str, patterns: List[str], *, max_items: int = 10, window: int = 220) -> List[str]:
    out: List[str] = []
    seen = set()
    for pat in patterns:
        try:
            for m in re.finditer(pat, text, flags=re.IGNORECASE | re.MULTILINE):
                snip = _snippet_around(text, m.start(), m.end(), window=window)
                snip = truncate(snip, 620)
                key = normalize_for_match(snip)[:220]
                if not snip or key in seen or is_placeholder_text(snip):
                    continue
                seen.add(key)
                out.append(snip)
                if len(out) >= max_items:
                    return out
        except re.error:
            continue
    return out


# Unités génériques rencontrées en sciences/ingénierie, pas propres à un domaine.
V20_UNIT_PATTERNS = {
    "temperature": [
        r"[-+]?\d+(?:[\.,]\d+)?\s*(?:°\s*C|°\s*F|K|kelvin|celsius)\b",
        r"\b(?:temperature|température)\s*(?:of|=|:|à|a)?\s*[-+]?\d+(?:[\.,]\d+)?\s*(?:°\s*C|K|celsius)?\b",
    ],
    "pressure": [
        r"\b\d+(?:[\.,]\d+)?\s*(?:Pa|kPa|MPa|GPa|bar|mbar|atm|psi)\b",
        r"\b(?:pressure|pression)\s*(?:of|=|:|à|a)?\s*\d+(?:[\.,]\d+)?\s*(?:Pa|kPa|MPa|bar|atm|psi)?\b",
    ],
    "time_duration": [
        r"\b\d+(?:[\.,]\d+)?\s*(?:s|sec|seconds?|min|minutes?|h|hours?|jours?|days?|semaines?|weeks?)\b",
        r"\b(?:duration|durée|time|temps|curing\s*time|aging\s*time)\s*(?:of|=|:|à|a)?\s*\d+(?:[\.,]\d+)?\s*(?:s|min|h|days?|jours?)?\b",
    ],
    "concentration_composition": [
        r"\b\d+(?:[\.,]\d+)?\s*(?:%|wt\.?%|vol\.?%|mol\.?%|M|mM|mol/L|g/L|mg/L|ppm|ppb)\b",
        r"\b(?:concentration|dosage|composition|ratio|rapport|fraction)\s*(?:of|=|:|à|a)?\s*\d+(?:[\.,]\d+)?\s*(?:%|M|mM|g/L|ppm)?\b",
    ],
    "ph": [
        r"\bpH\s*(?:=|:)?\s*\d+(?:[\.,]\d+)?\b",
    ],
    "mass_volume": [
        r"\b\d+(?:[\.,]\d+)?\s*(?:mg|g|kg|µg|ug|mL|ml|L|µL|uL|cm3|m3)\b",
    ],
    "length_dimension": [
        r"\b\d+(?:[\.,]\d+)?\s*(?:nm|µm|um|mm|cm|m|km|inch|in)\b",
        r"\b\d+(?:[\.,]\d+)?\s*[x×]\s*\d+(?:[\.,]\d+)?\s*(?:nm|µm|um|mm|cm|m|pixels?|px)?\b",
    ],
    "force_load_stress": [
        r"\b\d+(?:[\.,]\d+)?\s*(?:N|kN|MN|Pa|kPa|MPa|GPa)\b",
        r"\b(?:force|load|charge|contrainte|stress|strain|déformation|deformation)\s*(?:of|=|:)?\s*\d+(?:[\.,]\d+)?\s*(?:N|kN|MPa|GPa|%)?\b",
    ],
    "speed_flow_rate": [
        r"\b\d+(?:[\.,]\d+)?\s*(?:m/s|mm/s|km/h|rpm|tr/min|Hz|kHz|MHz|GHz)\b",
        r"\b\d+(?:[\.,]\d+)?\s*(?:mL/min|L/min|m3/s|kg/s|g/s)\b",
    ],
    "electrical": [
        r"\b\d+(?:[\.,]\d+)?\s*(?:V|mV|kV|A|mA|W|kW|MW|Ohm|Ω)\b",
    ],
    "angle_frequency": [
        r"\b\d+(?:[\.,]\d+)?\s*(?:°|deg|degrees?|rad)\b",
        r"\b\d+(?:[\.,]\d+)?\s*(?:Hz|kHz|MHz|GHz)\b",
    ],
}


V20_METHOD_AND_PROTOCOL_PATTERNS = {
    "named_methods_and_processes": [
        r"\b(?:method|méthode|approach|approche|procedure|procédure|protocol|protocole|process|procédé|technique|algorithm|algorithme|model|modèle|simulation|experiment|expérience|test|essai|assay|synthesis|synthèse|fabrication|manufacturing|formulation|characterization|caractérisation|measurement|mesure|calibration|optimisation|optimization)\b.{0,260}",
    ],
    "protocol_steps": [
        r"\b(?:first|then|next|finally|step|étape|ensuite|puis|après|avant|during|pendant|after|before)\b.{0,240}",
        r"\b(?:prepared|mixed|heated|cooled|cured|aged|dried|washed|filtered|measured|tested|simulated|calculated|optimized|trained|validated|compared)\b.{0,260}",
        r"\b(?:préparé|mélangé|chauffé|refroidi|durci|séché|lavé|filtré|mesuré|testé|simulé|calculé|optimisé|validé|comparé)\b.{0,260}",
    ],
    "controls_and_baselines": [
        r"\b(?:control|contrôle|baseline|reference|référence|standard|norme|without|avec et sans|blank|placebo|compar(?:ed|aison)|versus|vs\.?|ablation)\b.{0,260}",
    ],
    "standards_norms": [
        r"\b(?:ISO|ASTM|EN\s?\d+|NF\s?EN|DIN|IEC|AASHTO|ACI|Eurocode|standard|norme)\b.{0,180}",
    ],
    "instrumentation": [
        r"\b(?:microscope|SEM|TEM|XRD|XRF|FTIR|Raman|NMR|GC[- ]?MS|HPLC|DSC|TGA|DMA|spectrometer|spectroscopy|sensor|capteur|camera|caméra|strain\s*gauge|jauge|load\s*cell|cellule\s*de\s*charge|thermocouple|manometer|rheometer|viscometer|calorimeter|diffractometer)\b.{0,220}",
    ],
    "materials_components": [
        r"\b(?:material|matériau|materiau|sample|échantillon|specimen|composite|polymer|polymère|concrete|béton|cement|ciment|steel|acier|alloy|alliage|solvent|catalyst|catalyseur|reagent|réactif|mixture|mélange|aggregate|granulat|fiber|fibre)\b.{0,240}",
    ],
    "numerical_simulation": [
        r"\b(?:finite\s+element|FEM|FEA|CFD|mesh|maillage|grid|solver|time\s*step|boundary\s*condition|condition\s*limite|convergence|discretization|discrétisation|turbulence\s*model|ray\s*tracing|Monte\s*Carlo|simulation)\b.{0,280}",
    ],
    "machine_learning": [
        r"\b(?:training|entraînement|epochs?|époques?|batch\s*size|learning\s*rate|optimizer|loss|neural|CNN|transformer|random\s*forest|SVM|regression|classification|model\s*training)\b.{0,260}",
    ],
}


V20_METRIC_PATTERNS = {
    "performance_metrics": [
        r"\b(?:accuracy|precision|recall|F1|F-score|AUC|mAP|RMSE|MAE|MSE|R2|R\^2|error|erreur|yield|rendement|efficiency|efficacité|resistance|strength|stiffness|modulus|conductivity|permeability|porosity|density|viscosity|hardness|durability|lifetime|latency|throughput|cost|coût)\b\s*(?:of|=|:|à|a)?\s*[-+]?\d+(?:[\.,]\d+)?\s*%?",
        r"[-+]?\d+(?:[\.,]\d+)?\s*%\s*(?:increase|decrease|reduction|improvement|gain|loss|augmentation|réduction|amelioration|amélioration)",
    ],
    "statistical_metrics": [
        r"\b(?:p-value|p\s*<|confidence\s*interval|intervalle\s*de\s*confiance|standard\s*deviation|écart[- ]type|mean|moyenne|median|médiane|variance|correlation|corrélation)\b.{0,140}",
    ],
    "quality_thresholds": [
        r"\b(?:threshold|seuil|limit|limite|criterion|critère|acceptance|tolérance|tolerance)\b\s*(?:of|=|:)?\s*[-+]?\d+(?:[\.,]\d+)?\s*%?\b",
    ],
}


V20_MODEL_PATTERNS = {
    "epochs": TECHNICAL_DETAIL_PATTERNS.get("epochs", []),
    "batch_size": TECHNICAL_DETAIL_PATTERNS.get("batch_size", []),
    "learning_rate": TECHNICAL_DETAIL_PATTERNS.get("learning_rate", []),
    "optimizer": TECHNICAL_DETAIL_PATTERNS.get("optimizer", []),
    "loss_function": TECHNICAL_DETAIL_PATTERNS.get("loss_function", []),
    "dropout": TECHNICAL_DETAIL_PATTERNS.get("dropout", []),
    "weight_decay": TECHNICAL_DETAIL_PATTERNS.get("weight_decay", []),
    "layers": TECHNICAL_DETAIL_PATTERNS.get("layers", []),
    "input_shape": TECHNICAL_DETAIL_PATTERNS.get("input_shape", []),
    "patch_size": TECHNICAL_DETAIL_PATTERNS.get("patch_size", []),
}


def _v20_detect_procedure_types(source_text: str) -> List[str]:
    s = normalize_for_match(source_text)
    rules = [
        ("experimental_protocol", ["experiment", "experimental", "essai", "test", "measured", "measurement", "mesure", "protocol", "protocole"]),
        ("chemical_process_or_formulation", ["synthesis", "synthese", "réactif", "reagent", "solvent", "catalyst", "catalyseur", "ph", "concentration", "formulation"]),
        ("mechanical_or_material_testing", ["stress", "strain", "force", "load", "compression", "tensile", "flexural", "hardness", "modulus", "strength", "contrainte", "déformation", "traction"]),
        ("building_or_civil_engineering", ["concrete", "beton", "cement", "ciment", "mortar", "mortier", "aggregate", "granulat", "curing", "durcissement", "eurocode", "astm", "compressive strength"]),
        ("numerical_simulation_or_modeling", ["simulation", "finite element", "fem", "fea", "cfd", "mesh", "maillage", "solver", "boundary condition", "condition limite"]),
        ("machine_learning_or_statistical_model", ["training", "entraînement", "epoch", "learning rate", "optimizer", "neural", "cnn", "classification", "regression", "dataset"]),
        ("instrumentation_or_characterization", ["microscope", "xrd", "ftir", "raman", "nmr", "spectroscopy", "sensor", "capteur", "characterization", "caracterisation"]),
    ]
    detected = []
    for label, kws in rules:
        if any(normalize_for_match(k) in s for k in kws):
            detected.append(label)
    return detected or ["generic_scientific_method"]


def _v20_extract_unit_parameters(source_text: str) -> Dict[str, List[str]]:
    params: Dict[str, List[str]] = {}
    for name, pats in V20_UNIT_PATTERNS.items():
        vals = _v20_findall_unique(source_text, pats, max_items=14)
        if vals:
            params[name] = vals
    return params


def _v20_extract_method_and_protocol(source_text: str) -> Dict[str, Any]:
    return {
        "method_or_process_snippets": _v20_snippets(source_text, V20_METHOD_AND_PROTOCOL_PATTERNS["named_methods_and_processes"], max_items=10),
        "protocol_steps": _v20_snippets(source_text, V20_METHOD_AND_PROTOCOL_PATTERNS["protocol_steps"], max_items=10),
        "controls_baselines_references": _v20_snippets(source_text, V20_METHOD_AND_PROTOCOL_PATTERNS["controls_and_baselines"], max_items=8),
        "standards_or_norms": _v20_findall_unique(source_text, V20_METHOD_AND_PROTOCOL_PATTERNS["standards_norms"], max_items=10),
        "instrumentation": _v20_findall_unique(source_text, V20_METHOD_AND_PROTOCOL_PATTERNS["instrumentation"], max_items=12),
        "materials_or_components": _v20_snippets(source_text, V20_METHOD_AND_PROTOCOL_PATTERNS["materials_components"], max_items=10),
        "numerical_simulation_details": _v20_snippets(source_text, V20_METHOD_AND_PROTOCOL_PATTERNS["numerical_simulation"], max_items=10),
        "machine_learning_details": _v20_snippets(source_text, V20_METHOD_AND_PROTOCOL_PATTERNS["machine_learning"], max_items=8),
    }


def _v20_extract_model_training_details(source_text: str) -> Dict[str, List[str]]:
    details: Dict[str, List[str]] = {}
    for name, pats in V20_MODEL_PATTERNS.items():
        vals = _v20_findall_unique(source_text, pats, max_items=10)
        if vals:
            details[name] = vals
    return details


def _v20_extract_metrics(source_text: str) -> Dict[str, List[str]]:
    metrics: Dict[str, List[str]] = {}
    for name, pats in V20_METRIC_PATTERNS.items():
        vals = _v20_findall_unique(source_text, pats, max_items=14)
        if vals:
            metrics[name] = vals
    # Garder aussi les résultats quantitatifs déjà captés par l'ancienne V1.9.
    legacy = _v20_findall_unique(source_text, TECHNICAL_DETAIL_PATTERNS.get("metrics", []), max_items=14)
    if legacy:
        metrics.setdefault("task_metrics", [])
        metrics["task_metrics"] = unique_clean_list(metrics["task_metrics"] + legacy)[:14]
    return metrics


def build_technical_detail_profile(card: Dict[str, Any], technical: Dict[str, Any]) -> Dict[str, Any]:
    """
    V2.0 — Profil technique multi-domaine.

    Cette fonction ne cherche pas seulement les paramètres d'un modèle IA.
    Elle extrait, lorsque les sources les donnent explicitement :
    - méthode / protocole / procédé ;
    - étapes expérimentales ;
    - matériaux, échantillons, instruments ;
    - paramètres physiques/chimiques/mécaniques/numériques ;
    - conditions de simulation, maillage, solveur ;
    - hyperparamètres IA uniquement si le papier contient réellement un modèle entraîné ;
    - métriques, résultats, seuils, références, normes.

    Aucune valeur n'est inventée. Les champs absents restent vides.
    """
    source_text = build_technical_detail_source_text(card, technical)
    source_text = truncate(source_text, MULTIDOMAIN_TECH_MAX_SOURCE_CHARS)
    detected_types = _v20_detect_procedure_types(source_text)

    method_and_protocol = _v20_extract_method_and_protocol(source_text)
    measurable_parameters = _v20_extract_unit_parameters(source_text)
    metrics_and_results = _v20_extract_metrics(source_text)
    model_training = _v20_extract_model_training_details(source_text)

    data_and_protocol = {
        "classes": _v20_findall_unique(source_text, TECHNICAL_DETAIL_PATTERNS.get("classes", []), max_items=10),
        "sample_counts": _v20_findall_unique(source_text, TECHNICAL_DETAIL_PATTERNS.get("sample_counts", []), max_items=12),
        "dataset_or_sample_snippets": _v20_snippets(
            source_text,
            [
                r"\b(?:dataset|data\s*set|training\s+set|test\s+set|validation\s+set|benchmark|sample|specimen|échantillon|population|cohort|batch|lot)\b.{0,280}",
                r".{0,120}\b(?:classes|images|samples|échantillons|specimens|training|test|validation|mesures|measurements)\b.{0,200}",
            ],
            max_items=10,
        ),
        "validation_or_test_protocol_snippets": _v20_snippets(
            source_text,
            [
                r"\b(?:validation|evaluation|évaluation|experiment|expérience|test|essai|baseline|reference|référence|standard|norme|compare|comparison|comparaison|control|contrôle)\b.{0,300}",
            ],
            max_items=10,
        ),
    }

    # Ancienne compatibilité : architecture IA, seulement comme sous-bloc optionnel.
    architecture_or_system = {
        "architecture_or_system_snippets": _v20_snippets(
            source_text,
            [
                r"\b(?:architecture|network|réseau|model|modèle|backbone|encoder|decoder|module|system|système|setup|montage|apparatus|dispositif|reactor|réacteur|specimen|sample|échantillon|geometry|géométrie)\b.{0,280}",
            ],
            max_items=10,
        ),
        "layers": _v20_findall_unique(source_text, TECHNICAL_DETAIL_PATTERNS.get("layers", []), max_items=10),
        "input_shape_or_dimensions": unique_clean_list(
            _v20_findall_unique(source_text, TECHNICAL_DETAIL_PATTERNS.get("input_shape", []), max_items=10)
            + measurable_parameters.get("length_dimension", [])[:8]
        ),
        "patch_or_element_size": _v20_findall_unique(source_text, TECHNICAL_DETAIL_PATTERNS.get("patch_size", []), max_items=10),
    }

    implementation_parameters = {
        "hardware_or_machine": _v20_findall_unique(source_text, TECHNICAL_DETAIL_PATTERNS.get("hardware", []), max_items=12),
        "runtime_or_duration": unique_clean_list(
            _v20_findall_unique(source_text, TECHNICAL_DETAIL_PATTERNS.get("runtime", []), max_items=10)
            + measurable_parameters.get("time_duration", [])[:8]
        ),
        "software_solver_or_numerical_setup": method_and_protocol.get("numerical_simulation_details", []),
        "instrumentation": method_and_protocol.get("instrumentation", []),
    }

    # Détails exploitables par Phase 5, structurés par nature scientifique.
    scientific_detail_blocks = {
        "procedure_type_detected": detected_types,
        "method_and_protocol": method_and_protocol,
        "materials_samples_instruments": {
            "materials_or_components": method_and_protocol.get("materials_or_components", []),
            "instrumentation": method_and_protocol.get("instrumentation", []),
            "standards_or_norms": method_and_protocol.get("standards_or_norms", []),
        },
        "measurable_parameters_by_unit": measurable_parameters,
        "model_training_if_applicable": model_training,
        "architecture_or_system": architecture_or_system,
        "data_and_validation_protocol": data_and_protocol,
        "metrics_results_thresholds": metrics_and_results,
        "implementation_parameters": implementation_parameters,
    }

    missing_details: List[str] = []
    if not method_and_protocol.get("method_or_process_snippets") and not method_and_protocol.get("protocol_steps"):
        missing_details.append("méthode/protocole/étapes non trouvés explicitement")
    if not measurable_parameters:
        missing_details.append("paramètres numériques ou unités physiques/chimiques/mécaniques non trouvés explicitement")
    if not metrics_and_results:
        missing_details.append("métriques, résultats quantitatifs ou seuils non trouvés explicitement")
    if "machine_learning_or_statistical_model" in detected_types and not model_training:
        missing_details.append("hyperparamètres du modèle non trouvés explicitement malgré détection d'un modèle")
    if "numerical_simulation_or_modeling" in detected_types and not method_and_protocol.get("numerical_simulation_details"):
        missing_details.append("paramètres de simulation/maillage/solveur non trouvés explicitement")
    if any(t in detected_types for t in ["experimental_protocol", "chemical_process_or_formulation", "mechanical_or_material_testing", "building_or_civil_engineering"]):
        if not (method_and_protocol.get("materials_or_components") or method_and_protocol.get("instrumentation") or measurable_parameters):
            missing_details.append("conditions expérimentales, matériaux ou instrumentation non trouvés explicitement")

    # Score non sémantique : mesure la richesse extractive, pas la qualité scientifique.
    detail_score = 0
    for block in [method_and_protocol, measurable_parameters, metrics_and_results, model_training, architecture_or_system, data_and_protocol, implementation_parameters]:
        if isinstance(block, dict):
            for value in block.values():
                if isinstance(value, list) and value:
                    detail_score += min(4, len(value))
                elif isinstance(value, dict):
                    for sub in value.values():
                        if isinstance(sub, list) and sub:
                            detail_score += min(3, len(sub))
    detail_score = min(100, detail_score * 4)

    has_any_detail = bool(detail_score > 0)

    return {
        "profile_type": "phase_4_5_v2_0_multidomain_scientific_technical_details",
        "has_any_detail": has_any_detail,
        "detail_score": detail_score,
        "procedure_type_detected": detected_types,
        "scientific_method_profile": scientific_detail_blocks,
        # Compatibilité avec les versions précédentes / Phase 5 existante.
        "architecture": architecture_or_system,
        "training_hyperparameters": model_training,
        "data_and_protocol": data_and_protocol,
        "evaluation_metrics": metrics_and_results,
        "implementation_parameters": implementation_parameters,
        "measurable_parameters_by_unit": measurable_parameters,
        "method_and_experimental_protocol": method_and_protocol,
        "missing_details": unique_clean_list(missing_details),
        "evidence_language": "source_original_may_be_fr_or_en",
        "no_value_invented": True,
        "domain_specific_hardcoding": False,
        "phase_5_instruction": (
            "Utiliser ces détails pour enrichir l'état de l'art par la technique propre au domaine. "
            "Pour un modèle IA, discuter architecture/hyperparamètres si présents. "
            "Pour mécanique/chimie/bâtiment, discuter méthode expérimentale, matériau, conditions, paramètres, normes, métriques et résultats si présents. "
            "Si une valeur n'est pas explicitement trouvée, ne jamais l'inventer ; signaler seulement qu'elle n'est pas disponible dans les extraits."
        ),
    }


def summarize_technical_detail_profile(profile: Dict[str, Any], *, max_chars: int = 1100) -> str:
    if not isinstance(profile, dict) or not profile.get("has_any_detail"):
        return ""
    parts: List[str] = []

    types = profile.get("procedure_type_detected") or []
    if types:
        parts.append("type de méthode/procédé: " + ", ".join(unique_clean_list(types)[:4]))

    method_protocol = profile.get("method_and_experimental_protocol") or {}
    params = profile.get("measurable_parameters_by_unit") or {}
    metrics = profile.get("evaluation_metrics") or {}
    data = profile.get("data_and_protocol") or {}
    model = profile.get("training_hyperparameters") or {}
    impl = profile.get("implementation_parameters") or {}

    def add_values(label: str, values: List[str], n: int = 5):
        vals = [clean_text(x) for x in as_list(values) if clean_text(x)]
        if vals:
            parts.append(f"{label}: " + "; ".join(unique_clean_list(vals)[:n]))

    add_values("protocole/étapes", method_protocol.get("protocol_steps"), 3)
    add_values("matériaux/échantillons", method_protocol.get("materials_or_components"), 3)
    add_values("instruments/normes", unique_clean_list(as_list(method_protocol.get("instrumentation")) + as_list(method_protocol.get("standards_or_norms"))), 4)

    # Paramètres par unités : maximum quelques groupes pour éviter un payload trop lourd.
    for key in ["temperature", "pressure", "concentration_composition", "time_duration", "force_load_stress", "speed_flow_rate", "length_dimension", "ph"]:
        add_values(f"paramètres {key}", params.get(key), 4)

    # Si modèle IA présent, ajouter les hyperparamètres, mais ce n'est qu'un cas particulier.
    for key in ["epochs", "batch_size", "learning_rate", "optimizer", "loss_function", "layers"]:
        add_values(f"modèle {key}", model.get(key), 3)

    for key in ["performance_metrics", "task_metrics", "statistical_metrics", "quality_thresholds"]:
        add_values(f"métriques {key}", metrics.get(key), 4)

    add_values("données/protocole", unique_clean_list(as_list(data.get("classes")) + as_list(data.get("sample_counts"))), 4)
    add_values("implémentation", unique_clean_list(as_list(impl.get("hardware_or_machine")) + as_list(impl.get("runtime_or_duration"))), 4)

    return truncate(" | ".join(parts), max_chars)


def build_technical_detail_matrix(technical_methods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    matrix: List[Dict[str, Any]] = []
    for item in technical_methods:
        profile = item.get("technical_detail_profile") or {}
        scientific = profile.get("scientific_method_profile") or {}
        matrix.append({
            "citation_label": item.get("citation_label"),
            "method_or_concept": item.get("concept_label") or item.get("method_name") or item.get("technical_family"),
            "priority_tier": item.get("priority_tier"),
            "usage_type": item.get("usage_type"),
            "detail_score": profile.get("detail_score", 0),
            "has_any_detail": bool(profile.get("has_any_detail")),
            "procedure_type_detected": profile.get("procedure_type_detected") or [],
            "scientific_method_profile": scientific,
            "method_and_experimental_protocol": profile.get("method_and_experimental_protocol") or {},
            "measurable_parameters_by_unit": profile.get("measurable_parameters_by_unit") or {},
            "training_hyperparameters_if_model": profile.get("training_hyperparameters") or {},
            "architecture_or_system": profile.get("architecture") or {},
            "data_and_validation_protocol": profile.get("data_and_protocol") or {},
            "metrics_results_thresholds": profile.get("evaluation_metrics") or {},
            "implementation_parameters": profile.get("implementation_parameters") or {},
            "missing_details": profile.get("missing_details") or [],
            "technical_detail_summary_for_phase_5": summarize_technical_detail_profile(profile),
            "phase_5_instruction": profile.get("phase_5_instruction"),
        })
    return matrix



# ============================================================
# V2.1 — Open-domain no-loss technical extraction override
# ============================================================

OPEN_DOMAIN_MAX_SNIPPETS = 30
OPEN_DOMAIN_MAX_SOURCE_CHARS = 22000


def _v21_sentence_split(text: Any) -> List[str]:
    s = clean_text(text)
    # Conserver aussi les lignes : les papiers contiennent souvent des pseudo-tableaux.
    chunks: List[str] = []
    for block in re.split(r"\n+", s):
        block = clean_text(block)
        if not block:
            continue
        parts = re.split(r"(?<=[.!?;])\s+(?=[A-ZÀ-Ý0-9])", block)
        for p in parts:
            p = clean_text(p)
            if 35 <= len(p) <= 900:
                chunks.append(p)
            elif len(p) > 900:
                # Découpage prudent des paragraphes longs, sans perdre le contenu entier.
                for i in range(0, min(len(p), 2400), 650):
                    sub = clean_text(p[i:i + 750])
                    if len(sub) >= 35:
                        chunks.append(sub)
    return chunks


def _v21_has_numeric_or_symbolic_detail(sentence: str) -> bool:
    s = clean_text(sentence)
    if not s:
        return False
    # Valeur numérique, intervalle, pourcentage, formule, symbole grec, variable = valeur.
    return bool(
        re.search(r"\b\d+(?:[.,]\d+)?\s*(?:%|°C|K|Pa|kPa|MPa|GPa|bar|mbar|N|kN|Hz|kHz|MHz|GHz|V|mV|A|mA|W|kW|J|kJ|mol|mmol|g|mg|kg|L|mL|µL|s|min|h|ms|µs|nm|µm|mm|cm|m|rpm|m/s|mm/s|m²|m3|m\^2|m\^3)?\b", s)
        or re.search(r"\b[A-Za-zΑ-ωα-ω][A-Za-z0-9_\-]{0,12}\s*[=:=]\s*[-+]?\d", s)
        or re.search(r"[α-ωΑ-Ω]\s*[=:=]?\s*[-+]?\d", s)
        or re.search(r"\b(?:R2|R\^2|RMSE|MAE|MSE|AUC|F1|p\s*value|CI|IC95|SNR|PSNR|IoU)\b", s, flags=re.IGNORECASE)
    )


def _v21_has_protocol_or_method_signal(sentence: str) -> bool:
    n = normalize_for_match(sentence)
    if not n:
        return False
    # Signaux transversaux, pas liés à un domaine spécifique.
    generic_terms = [
        "method", "approach", "protocol", "procedure", "process", "workflow", "pipeline",
        "step", "setup", "configuration", "parameter", "condition", "baseline", "control",
        "reference", "standard", "benchmark", "measurement", "measure", "test", "experiment",
        "evaluate", "evaluation", "validation", "calibration", "sample", "specimen", "dataset",
        "méthode", "approche", "protocole", "procédure", "procédé", "processus", "étape",
        "paramètre", "condition", "témoin", "référence", "norme", "mesure", "essai",
        "expérience", "évaluation", "validation", "calibration", "échantillon",
    ]
    return any(t in n for t in generic_terms)


def _v21_has_named_object_signal(sentence: str) -> bool:
    s = clean_text(sentence)
    if not s:
        return False
    # Noms potentiels de méthode/système/matériau : CamelCase, acronymes, normes, références techniques.
    return bool(
        re.search(r"\b[A-Z]{2,8}(?:[-_/][A-Z0-9]{1,8})?\b", s)
        or re.search(r"\b[A-Z][a-z]+[A-Z][A-Za-z0-9]*\b", s)
        or re.search(r"\b(?:ISO|ASTM|EN|DIN|NF|IEC|IEEE|API|AASHTO)\s*[-:]?\s*[A-Z0-9\-]+\b", s, flags=re.IGNORECASE)
        or re.search(r"\b[A-Za-z][A-Za-z0-9_\-]{2,}\s*(?:method|model|algorithm|process|protocol|test|assay|index|score|ratio|coefficient)\b", s, flags=re.IGNORECASE)
    )


def _v21_is_technical_candidate(sentence: str) -> bool:
    s = clean_text(sentence)
    if len(s) < 35:
        return False
    n = normalize_for_match(s)
    # Écarter uniquement les phrases bibliographiques/admin très faibles.
    if any(x in n for x in ["copyright", "all rights reserved", "received revised accepted", "corresponding author"]):
        return False
    return (
        _v21_has_numeric_or_symbolic_detail(s)
        or _v21_has_protocol_or_method_signal(s)
        or _v21_has_named_object_signal(s)
    )


def _v21_extract_explicit_numeric_expressions(source_text: str, max_items: int = 80) -> List[str]:
    patterns = [
        r"\b[A-Za-zÀ-ÿα-ωΑ-Ω][A-Za-zÀ-ÿ0-9_\-]{0,25}\s*(?:=|:|≈|~|<|>|≤|≥)\s*[-+]?\d+(?:[.,]\d+)?(?:\s*(?:%|°C|K|Pa|kPa|MPa|GPa|bar|N|kN|Hz|kHz|MHz|GHz|V|A|W|J|mol|g|mg|kg|L|mL|s|min|h|ms|nm|µm|mm|cm|m))?",
        r"\b\d+(?:[.,]\d+)?\s*(?:%|°C|K|Pa|kPa|MPa|GPa|bar|N|kN|Hz|kHz|MHz|GHz|V|mV|A|mA|W|kW|J|kJ|mol|mmol|g|mg|kg|L|mL|µL|s|min|h|ms|µs|nm|µm|mm|cm|m|rpm|m/s|mm/s)\b",
        r"\b\d+(?:[.,]\d+)?\s*(?:x|×|\*)\s*\d+(?:[.,]\d+)?(?:\s*(?:x|×|\*)\s*\d+(?:[.,]\d+)?)?\b",
        r"\b(?:from|de|entre|between)\s+[-+]?\d+(?:[.,]\d+)?\s+(?:to|à|and|et)\s+[-+]?\d+(?:[.,]\d+)?\b",
    ]
    return _v20_findall_unique(source_text, patterns, max_items=max_items)


def _v21_extract_equations_or_formulae(source_text: str, max_items: int = 30) -> List[str]:
    snippets = []
    for sent in _v21_sentence_split(source_text):
        if re.search(r"[A-Za-zα-ωΑ-Ω0-9_\)\]]\s*[=≈]\s*[^.]{3,120}", sent) or re.search(r"\b(?:Eq\.|Equation|équation|formula|formule)\b", sent, flags=re.IGNORECASE):
            snippets.append(truncate(sent, 500))
        if len(snippets) >= max_items:
            break
    return unique_clean_list(snippets)


def _v21_extract_parameter_names(source_text: str, max_items: int = 60) -> List[str]:
    params: List[str] = []
    patterns = [
        r"\b(?:parameter|paramètre|variable|coefficient|factor|facteur|ratio|threshold|seuil)\s+([A-Za-zα-ωΑ-Ω][A-Za-z0-9_\-]{0,25})\b",
        r"\b([A-Za-zα-ωΑ-Ω][A-Za-z0-9_\-]{0,25})\s*(?:=|:|≈|~|<|>|≤|≥)\s*[-+]?\d",
        r"\b([A-Z]{2,8})\s*(?:is|are|corresponds|désigne|signifie|stands for)\b",
    ]
    for pat in patterns:
        for m in re.finditer(pat, source_text, flags=re.IGNORECASE):
            val = clean_text(m.group(1))
            if val and not is_generic_method_name(val):
                params.append(val)
            if len(params) >= max_items:
                return unique_clean_list(params)
    return unique_clean_list(params)


def _v21_extract_unclassified_technical_evidence(source_text: str, max_items: int = OPEN_DOMAIN_MAX_SNIPPETS) -> Dict[str, Any]:
    sentences = _v21_sentence_split(source_text)
    selected: List[str] = []
    seen = set()
    for sent in sentences:
        if not _v21_is_technical_candidate(sent):
            continue
        key = normalize_for_match(sent)[:220]
        if key in seen:
            continue
        seen.add(key)
        selected.append(truncate(sent, 650))
        if len(selected) >= max_items:
            break

    numeric = _v21_extract_explicit_numeric_expressions(source_text)
    equations = _v21_extract_equations_or_formulae(source_text)
    parameter_names = _v21_extract_parameter_names(source_text)

    return {
        "unclassified_technical_snippets": selected,
        "explicit_numeric_expressions": numeric,
        "equations_or_formulae": equations,
        "named_variables_or_parameters": parameter_names,
        "has_open_domain_evidence": bool(selected or numeric or equations or parameter_names),
        "preservation_rule": (
            "Ces éléments sont conservés même si le domaine ou le type de méthode n'est pas reconnu. "
            "Phase 5 peut les utiliser comme détails techniques sourcés, sans inventer leur catégorie."
        ),
    }


def _v21_merge_preserved_evidence_into_known_blocks(profile: Dict[str, Any], open_evidence: Dict[str, Any]) -> None:
    """
    Ne remplace pas les catégories connues. Ajoute seulement une couche no-loss.
    """
    sci = profile.setdefault("scientific_method_profile", {})
    sci["open_domain_technical_evidence"] = open_evidence
    profile["open_domain_technical_evidence"] = open_evidence
    profile["unknown_domain_preservation_enabled"] = True
    profile["domain_specific_hardcoding"] = False
    profile["no_value_invented"] = True

    # Si aucun domaine connu n'est détecté mais qu'on a des preuves ouvertes, ne pas déclarer le profil vide.
    if open_evidence.get("has_open_domain_evidence"):
        profile["has_any_detail"] = True
        profile["detail_score"] = max(safe_int(profile.get("detail_score"), 0), min(100, 20 + len(open_evidence.get("unclassified_technical_snippets") or []) * 4))

    # Missing details doit informer mais pas faire croire que l'information est perdue.
    missing = profile.get("missing_details") or []
    if open_evidence.get("has_open_domain_evidence"):
        missing = [m for m in missing if "non trouv" not in normalize_for_match(m) or "métriques" in m or "paramètres" in m]
        missing.append("certaines preuves techniques sont conservées en bloc ouvert car non classées automatiquement")
    profile["missing_details"] = unique_clean_list(missing)


def build_technical_detail_profile(card: Dict[str, Any], technical: Dict[str, Any]) -> Dict[str, Any]:
    """
    V2.1 — Profil technique multi-domaine + couche ouverte no-loss.

    Différence avec V2.0 : si le domaine ou le type de détail n'est pas reconnu,
    on ne perd pas l'information. Tous les snippets techniques détectables par formes
    générales (valeurs, unités, équations, variables, protocoles, normes, noms de méthodes)
    sont conservés dans open_domain_technical_evidence.
    """
    source_text = build_technical_detail_source_text(card, technical)
    source_text = truncate(source_text, OPEN_DOMAIN_MAX_SOURCE_CHARS)

    detected_types = _v20_detect_procedure_types(source_text)
    method_and_protocol = _v20_extract_method_and_protocol(source_text)
    measurable_parameters = _v20_extract_unit_parameters(source_text)
    metrics_and_results = _v20_extract_metrics(source_text)
    model_training = _v20_extract_model_training_details(source_text)
    open_evidence = _v21_extract_unclassified_technical_evidence(source_text)

    data_and_protocol = {
        "classes": _v20_findall_unique(source_text, TECHNICAL_DETAIL_PATTERNS.get("classes", []), max_items=10),
        "sample_counts": _v20_findall_unique(source_text, TECHNICAL_DETAIL_PATTERNS.get("sample_counts", []), max_items=12),
        "dataset_or_sample_snippets": _v20_snippets(
            source_text,
            [
                r"\b(?:dataset|data\s*set|training\s+set|test\s+set|validation\s+set|benchmark|sample|specimen|échantillon|population|cohort|batch|lot)\b.{0,300}",
                r".{0,140}\b(?:classes|images|samples|échantillons|specimens|training|test|validation|mesures|measurements|observations)\b.{0,220}",
            ],
            max_items=10,
        ),
        "validation_or_test_protocol_snippets": _v20_snippets(
            source_text,
            [
                r"\b(?:validation|evaluation|évaluation|experiment|expérience|test|essai|baseline|reference|référence|standard|norme|compare|comparison|comparaison|control|contrôle|calibration)\b.{0,320}",
            ],
            max_items=10,
        ),
    }

    architecture_or_system = {
        "architecture_or_system_snippets": _v20_snippets(
            source_text,
            [
                r"\b(?:architecture|network|réseau|model|modèle|backbone|encoder|decoder|module|system|système|setup|montage|apparatus|dispositif|reactor|réacteur|specimen|sample|échantillon|geometry|géométrie|process|procédé|procedure|procédure)\b.{0,300}",
            ],
            max_items=10,
        ),
        "layers": _v20_findall_unique(source_text, TECHNICAL_DETAIL_PATTERNS.get("layers", []), max_items=10),
        "input_shape_or_dimensions": unique_clean_list(
            _v20_findall_unique(source_text, TECHNICAL_DETAIL_PATTERNS.get("input_shape", []), max_items=10)
            + measurable_parameters.get("length_dimension", [])[:8]
        ),
        "patch_or_element_size": _v20_findall_unique(source_text, TECHNICAL_DETAIL_PATTERNS.get("patch_size", []), max_items=10),
    }

    implementation_parameters = {
        "hardware_or_machine": _v20_findall_unique(source_text, TECHNICAL_DETAIL_PATTERNS.get("hardware", []), max_items=12),
        "runtime_or_duration": unique_clean_list(
            _v20_findall_unique(source_text, TECHNICAL_DETAIL_PATTERNS.get("runtime", []), max_items=10)
            + measurable_parameters.get("time_duration", [])[:8]
        ),
        "software_solver_or_numerical_setup": method_and_protocol.get("numerical_simulation_details", []),
        "instrumentation": method_and_protocol.get("instrumentation", []),
    }

    scientific_detail_blocks = {
        "procedure_type_detected": detected_types,
        "method_and_protocol": method_and_protocol,
        "materials_samples_instruments": {
            "materials_or_components": method_and_protocol.get("materials_or_components", []),
            "instrumentation": method_and_protocol.get("instrumentation", []),
            "standards_or_norms": method_and_protocol.get("standards_or_norms", []),
        },
        "measurable_parameters_by_unit": measurable_parameters,
        "model_training_if_applicable": model_training,
        "architecture_or_system": architecture_or_system,
        "data_and_validation_protocol": data_and_protocol,
        "metrics_results_thresholds": metrics_and_results,
        "implementation_parameters": implementation_parameters,
        "open_domain_technical_evidence": open_evidence,
    }

    missing_details: List[str] = []
    if not method_and_protocol.get("method_or_process_snippets") and not method_and_protocol.get("protocol_steps"):
        missing_details.append("méthode/protocole/étapes non classés dans les catégories connues")
    if not measurable_parameters:
        missing_details.append("paramètres numériques/unités non classés dans les catégories connues")
    if not metrics_and_results:
        missing_details.append("métriques/résultats quantitatifs non classés dans les catégories connues")
    if "machine_learning_or_statistical_model" in detected_types and not model_training:
        missing_details.append("hyperparamètres du modèle non trouvés explicitement malgré détection d'un modèle")
    if "numerical_simulation_or_modeling" in detected_types and not method_and_protocol.get("numerical_simulation_details"):
        missing_details.append("paramètres de simulation/maillage/solveur non trouvés explicitement")
    if open_evidence.get("has_open_domain_evidence"):
        missing_details.append("preuves techniques non classées conservées dans open_domain_technical_evidence")

    detail_score = 0
    for block in [method_and_protocol, measurable_parameters, metrics_and_results, model_training, architecture_or_system, data_and_protocol, implementation_parameters, open_evidence]:
        if isinstance(block, dict):
            for value in block.values():
                if isinstance(value, list) and value:
                    detail_score += min(5, len(value))
                elif isinstance(value, dict):
                    for sub in value.values():
                        if isinstance(sub, list) and sub:
                            detail_score += min(4, len(sub))
    detail_score = min(100, detail_score * 3)

    profile = {
        "profile_type": "phase_4_5_v2_1_open_domain_no_loss_technical_details",
        "has_any_detail": bool(detail_score > 0),
        "detail_score": detail_score,
        "procedure_type_detected": detected_types,
        "scientific_method_profile": scientific_detail_blocks,
        "architecture": architecture_or_system,
        "training_hyperparameters": model_training,
        "data_and_protocol": data_and_protocol,
        "evaluation_metrics": metrics_and_results,
        "implementation_parameters": implementation_parameters,
        "measurable_parameters_by_unit": measurable_parameters,
        "method_and_experimental_protocol": method_and_protocol,
        "open_domain_technical_evidence": open_evidence,
        "missing_details": unique_clean_list(missing_details),
        "evidence_language": "source_original_may_be_fr_or_en",
        "no_value_invented": True,
        "domain_specific_hardcoding": False,
        "unknown_domain_preservation_enabled": True,
        "phase_5_instruction": (
            "Utiliser d'abord les champs classés. Si le domaine ou le détail n'est pas reconnu, utiliser le bloc "
            "open_domain_technical_evidence comme réserve technique sourcée. Ne jamais ignorer ces preuves ouvertes ; "
            "les reformuler prudemment sans inventer leur catégorie ni leur valeur."
        ),
    }
    _v21_merge_preserved_evidence_into_known_blocks(profile, open_evidence)
    return profile


def summarize_technical_detail_profile(profile: Dict[str, Any], *, max_chars: int = 1300) -> str:
    if not isinstance(profile, dict) or not profile.get("has_any_detail"):
        return ""

    parts: List[str] = []
    types = profile.get("procedure_type_detected") or []
    if types:
        parts.append("type de méthode/procédé: " + ", ".join(unique_clean_list(types)[:4]))

    method_protocol = profile.get("method_and_experimental_protocol") or {}
    params = profile.get("measurable_parameters_by_unit") or {}
    metrics = profile.get("evaluation_metrics") or {}
    data = profile.get("data_and_protocol") or {}
    model = profile.get("training_hyperparameters") or {}
    implementation = profile.get("implementation_parameters") or {}
    open_ev = profile.get("open_domain_technical_evidence") or {}

    if method_protocol.get("method_or_process_snippets"):
        parts.append("méthode/procédé: " + " | ".join(method_protocol.get("method_or_process_snippets")[:2]))
    if method_protocol.get("protocol_steps"):
        parts.append("étapes/protocole: " + " | ".join(method_protocol.get("protocol_steps")[:2]))
    if params:
        flat = []
        for k, vals in params.items():
            for v in vals[:3]:
                flat.append(f"{k}={v}")
        if flat:
            parts.append("paramètres mesurables: " + "; ".join(flat[:8]))
    if metrics:
        flat = []
        for k, vals in metrics.items():
            for v in vals[:3]:
                flat.append(f"{k}={v}")
        if flat:
            parts.append("métriques/résultats: " + "; ".join(flat[:8]))
    if data.get("dataset_or_sample_snippets"):
        parts.append("données/échantillons: " + " | ".join(data.get("dataset_or_sample_snippets")[:2]))
    if model:
        flat = []
        for k, vals in model.items():
            for v in vals[:2]:
                flat.append(f"{k}={v}")
        if flat:
            parts.append("hyperparamètres/modèle si applicable: " + "; ".join(flat[:8]))
    if implementation.get("instrumentation"):
        parts.append("instrumentation: " + "; ".join(implementation.get("instrumentation")[:5]))
    if open_ev.get("unclassified_technical_snippets"):
        parts.append("preuves techniques non classées: " + " | ".join(open_ev.get("unclassified_technical_snippets")[:3]))
    if open_ev.get("explicit_numeric_expressions"):
        parts.append("valeurs/variables conservées: " + "; ".join(open_ev.get("explicit_numeric_expressions")[:8]))

    return truncate(" ; ".join([p for p in parts if clean_text(p)]), max_chars)


# Compat metadata override
PHASE_4_5_V2_1_OPEN_DOMAIN_NO_LOSS = True


# ============================================================
# V2.2 — Result-to-method/test/technology linking (no-loss, no hardcoding)
# ============================================================

PHASE_4_5_V2_2_RESULT_METHOD_LINKING = True

# Keep previous V2.1 implementation available.
_v21_build_technical_detail_profile = build_technical_detail_profile

V22_RESULT_SIGNAL_TERMS = [
    "result", "results", "show", "shows", "showed", "demonstrate", "demonstrates", "demonstrated",
    "achieve", "achieves", "achieved", "obtain", "obtains", "obtained", "improve", "improves", "improved",
    "increase", "increases", "increased", "decrease", "decreases", "decreased", "reduce", "reduces", "reduced",
    "outperform", "outperforms", "outperformed", "gain", "gains", "loss", "error", "accuracy", "precision",
    "recall", "f1", "auc", "rmse", "mae", "mse", "r2", "r^2", "yield", "rendement", "conversion",
    "strength", "resistance", "résistance", "contrainte", "stress", "strain", "déformation", "module",
    "porosity", "porosité", "viscosity", "viscosité", "conductivity", "conductivité", "efficiency",
    "efficacité", "performance", "convergence", "tolerance", "tolérance", "threshold", "seuil",
    "résultat", "résultats", "montre", "montrent", "démontre", "démontrent", "atteint", "obtenu",
    "améliore", "amélioration", "réduit", "réduction", "augmente", "augmentation", "diminue", "diminution",
]

V22_TEST_PROTOCOL_SIGNAL_TERMS = [
    "test", "experiment", "experimental", "evaluation", "validation", "benchmark", "baseline", "control",
    "reference", "standard", "protocol", "trial", "assay", "measurement", "measurements", "calibration",
    "simulation", "solver", "mesh", "grid", "dataset", "training set", "test set", "validation set",
    "essai", "expérience", "expérimental", "évaluation", "validation", "référence", "norme", "protocole",
    "mesure", "mesures", "calibration", "simulation", "solveur", "maillage", "jeu de données",
]

V22_METHOD_TECH_SIGNAL_TERMS = [
    "method", "approach", "technique", "algorithm", "model", "architecture", "framework", "pipeline",
    "process", "procedure", "system", "instrument", "device", "sensor", "reactor", "material", "specimen",
    "sample", "formulation", "treatment", "preparation", "solver", "mesh", "grid", "classifier",
    "méthode", "approche", "technique", "algorithme", "modèle", "architecture", "procédé", "procédure",
    "système", "instrument", "capteur", "réacteur", "matériau", "échantillon", "formulation", "traitement",
]


def _v22_sentence_index(text: Any) -> List[Dict[str, Any]]:
    sentences = _v21_sentence_split(text)
    out = []
    pos = 0
    for i, sent in enumerate(sentences):
        # Approximate position for neighborhood; robust enough for ranking, not citation.
        idx = clean_text(text).find(sent, pos)
        if idx < 0:
            idx = pos
        pos = idx + len(sent)
        out.append({"index": i, "char_start": idx, "text": sent})
    return out


def _v22_contains_any(text: Any, terms: List[str]) -> bool:
    n = normalize_for_match(text)
    return any(normalize_for_match(t) in n for t in terms if t)


def _v22_is_result_sentence(sentence: str) -> bool:
    if not clean_text(sentence):
        return False
    has_metric = bool(_v20_extract_metrics(sentence))
    has_number = _v21_has_numeric_or_symbolic_detail(sentence)
    has_result_signal = _v22_contains_any(sentence, V22_RESULT_SIGNAL_TERMS)
    # On évite de prendre toutes les phrases numériques : résultat = signal résultat + métrique/valeur,
    # ou métrique forte détectée explicitement.
    return bool((has_result_signal and (has_metric or has_number)) or has_metric)


def _v22_context_sentences(indexed: List[Dict[str, Any]], center_index: int, radius: int = 3) -> List[Dict[str, Any]]:
    return [s for s in indexed if abs(int(s.get("index", 0)) - center_index) <= radius and s.get("index") != center_index]


def _v22_rank_context_sentence(result_sentence: str, candidate: Dict[str, Any], signal_terms: List[str]) -> float:
    text = candidate.get("text") or ""
    score = 0.0
    if _v22_contains_any(text, signal_terms):
        score += 3.0
    if _v21_has_numeric_or_symbolic_detail(text):
        score += 0.8
    if evidence_overlap_score(result_sentence, text) > 0:
        score += min(2.0, evidence_overlap_score(result_sentence, text) * 5.0)
    # Prefer concise technical context, not long raw paragraphs.
    if 45 <= len(text) <= 450:
        score += 0.6
    return round(score, 3)


def _v22_best_context(indexed: List[Dict[str, Any]], center_index: int, result_sentence: str, signal_terms: List[str], max_items: int = 2) -> List[str]:
    candidates = _v22_context_sentences(indexed, center_index, radius=3)
    ranked = []
    for c in candidates:
        sc = _v22_rank_context_sentence(result_sentence, c, signal_terms)
        if sc > 0:
            ranked.append((sc, c.get("text") or ""))
    ranked.sort(key=lambda x: (-x[0], len(x[1])))
    return unique_clean_list([truncate(x[1], 420) for x in ranked[:max_items]])


def _v22_extract_result_metric_values(sentence: str) -> Dict[str, Any]:
    metrics = _v20_extract_metrics(sentence)
    explicit_numbers = _v21_extract_explicit_numeric_expressions(sentence, max_items=20)
    equations = _v21_extract_equations_or_formulae(sentence, max_items=10)
    variables = _v21_extract_parameter_names(sentence, max_items=15)
    return {
        "metrics_detected": metrics,
        "explicit_numeric_expressions": explicit_numbers,
        "equations_or_formulae": equations,
        "named_variables_or_parameters": variables,
    }


def _v22_result_type(sentence: str) -> str:
    n = normalize_for_match(sentence)
    if any(t in n for t in ["accuracy", "precision", "recall", "f1", "auc", "classification", "classifier", "erreur", "error"]):
        return "model_or_classification_performance"
    if any(t in n for t in ["strength", "resistance", "contrainte", "stress", "strain", "fatigue", "compression", "traction"]):
        return "mechanical_or_material_performance"
    if any(t in n for t in ["yield", "rendement", "conversion", "ph", "concentration", "catalyst", "catalyseur"]):
        return "chemical_or_process_performance"
    if any(t in n for t in ["rmse", "mae", "r2", "r^2", "convergence", "mesh", "maillage", "solver", "simulation"]):
        return "simulation_or_numerical_validation"
    if any(t in n for t in ["time", "runtime", "latency", "speed", "cpu", "gpu", "mémoire", "memory"]):
        return "implementation_or_runtime_performance"
    return "open_domain_result"


def _v22_extract_result_test_method_links(source_text: str, profile: Dict[str, Any], max_results: int = 18) -> Dict[str, Any]:
    indexed = _v22_sentence_index(source_text)
    result_claims: List[Dict[str, Any]] = []
    seen = set()

    for sent_obj in indexed:
        sent = clean_text(sent_obj.get("text"))
        if not _v22_is_result_sentence(sent):
            continue
        key = normalize_for_match(sent)[:220]
        if key in seen:
            continue
        seen.add(key)

        idx = int(sent_obj.get("index", 0))
        metrics = _v22_extract_result_metric_values(sent)
        method_context = _v22_best_context(indexed, idx, sent, V22_METHOD_TECH_SIGNAL_TERMS, max_items=2)
        test_context = _v22_best_context(indexed, idx, sent, V22_TEST_PROTOCOL_SIGNAL_TERMS, max_items=2)

        # Fallback depuis les blocs déjà classés si le voisinage ne suffit pas.
        method_protocol = profile.get("method_and_experimental_protocol") or {}
        data_protocol = profile.get("data_and_protocol") or {}
        architecture = profile.get("architecture") or {}
        implementation = profile.get("implementation_parameters") or {}

        if not method_context:
            method_context = unique_clean_list(
                (method_protocol.get("method_or_process_snippets") or [])[:1]
                + (architecture.get("architecture_or_system_snippets") or [])[:1]
                + (implementation.get("software_solver_or_numerical_setup") or [])[:1]
            )[:2]
        if not test_context:
            test_context = unique_clean_list(
                (data_protocol.get("validation_or_test_protocol_snippets") or [])[:1]
                + (data_protocol.get("dataset_or_sample_snippets") or [])[:1]
                + (method_protocol.get("controls_baselines_references") or [])[:1]
            )[:2]

        result_claims.append({
            "result_id": f"R{len(result_claims)+1}",
            "result_text": truncate(sent, 520),
            "result_type": _v22_result_type(sent),
            "metrics_and_values": metrics,
            "linked_method_or_technology_context": method_context,
            "linked_test_or_validation_context": test_context,
            "evidence_window": {
                "previous_sentence": truncate((indexed[idx-1].get("text") if idx > 0 else ""), 360),
                "next_sentence": truncate((indexed[idx+1].get("text") if idx + 1 < len(indexed) else ""), 360),
            },
            "link_confidence": "high" if method_context and test_context else ("medium" if method_context or test_context else "low"),
            "phase_5_instruction": (
                "Ne pas citer ce résultat seul. Le rattacher explicitement à la méthode/technologie, au test/protocole, "
                "aux données et à la métrique détectée. Si le lien est medium/low, formuler prudemment et signaler que "
                "le protocole précis doit être vérifié dans la source."
            ),
            "no_value_invented": True,
        })
        if len(result_claims) >= max_results:
            break

    by_type: Dict[str, int] = {}
    for r in result_claims:
        by_type[r["result_type"]] = by_type.get(r["result_type"], 0) + 1

    return {
        "profile_type": "phase_4_5_v2_2_result_to_method_test_linking",
        "has_linked_results": bool(result_claims),
        "result_claims": result_claims,
        "result_count": len(result_claims),
        "result_types_count": by_type,
        "no_result_discarded_because_domain_unknown": True,
        "domain_specific_hardcoding": False,
        "phase_5_instruction": (
            "Utiliser ces result_claims pour enrichir l'état de l'art : chaque résultat doit être expliqué avec "
            "son test/protocole, sa méthode/technologie, ses données et sa métrique. Ne pas écrire une liste de résultats."
        ),
    }


def build_technical_detail_profile(card: Dict[str, Any], technical: Dict[str, Any]) -> Dict[str, Any]:
    """
    V2.2 — ajoute le chaînage Résultat -> Test/Protocole -> Méthode/Technologie -> Métrique.

    Objectif : exploiter les résultats dans l'état de l'art sans les isoler de leur contexte expérimental.
    Multi-domaine, no-loss, pas de hardcoding projet/article/domaine.
    """
    profile = _v21_build_technical_detail_profile(card, technical)
    source_text = build_technical_detail_source_text(card, technical)
    source_text = truncate(source_text, OPEN_DOMAIN_MAX_SOURCE_CHARS)
    result_links = _v22_extract_result_test_method_links(source_text, profile)
    profile["result_method_test_links"] = result_links
    profile["profile_type"] = "phase_4_5_v2_2_open_domain_result_method_test_linking"
    profile["phase_5_instruction"] = (
        profile.get("phase_5_instruction", "")
        + " Utiliser result_method_test_links pour rattacher chaque résultat à son protocole, sa méthode/technologie, "
          "ses données et sa métrique. Les résultats non reliés avec confiance forte doivent rester prudents."
    ).strip()
    if result_links.get("has_linked_results"):
        profile["has_any_detail"] = True
        profile["detail_score"] = min(100, int(profile.get("detail_score") or 0) + min(20, result_links.get("result_count", 0) * 3))
    return profile


_v21_summarize_technical_detail_profile = summarize_technical_detail_profile

def summarize_technical_detail_profile(profile: Dict[str, Any], *, max_chars: int = 1500) -> str:
    base = ""
    try:
        base = _v21_summarize_technical_detail_profile(profile, max_chars=max_chars)
    except NameError:
        # Fallback minimal if alias is unavailable.
        base = ""
    result_links = (profile or {}).get("result_method_test_links") or {}
    claims = result_links.get("result_claims") or []
    parts = [base] if base else []
    if claims:
        rendered = []
        for r in claims[:4]:
            metric_bits = []
            mav = r.get("metrics_and_values") or {}
            for _, vals in (mav.get("metrics_detected") or {}).items():
                metric_bits.extend(vals[:2])
            if not metric_bits:
                metric_bits.extend((mav.get("explicit_numeric_expressions") or [])[:2])
            method_ctx = "; ".join((r.get("linked_method_or_technology_context") or [])[:1])
            test_ctx = "; ".join((r.get("linked_test_or_validation_context") or [])[:1])
            item = f"{r.get('result_id')}: {r.get('result_type')}"
            if metric_bits:
                item += " | métrique/valeur=" + ", ".join(unique_clean_list(metric_bits)[:3])
            if method_ctx:
                item += " | méthode/techno=" + truncate(method_ctx, 180)
            if test_ctx:
                item += " | test/protocole=" + truncate(test_ctx, 180)
            rendered.append(item)
        parts.append("résultats reliés méthode-test: " + " || ".join(rendered))
    return truncate(" ; ".join([p for p in parts if clean_text(p)]), max_chars)




# ============================================================
# V2.3 — Result metric no-loss hardening
# ============================================================
# Pourquoi :
# - En V2.2, certains result_claims avaient metrics_and_values vide parce que
#   la phrase contenait un résultat qualitatif, ou une valeur approximative
#   ("almost 95 %", "up to 9.14%", "x times", "few", etc.) non captée par les regex.
# - La règle V2.3 est no-loss : un résultat n'est jamais perdu uniquement parce que
#   sa métrique n'est pas reconnue. On conserve le texte, les valeurs brutes,
#   les fenêtres de contexte et un statut d'extraction.

V23_APPROX_WORDS_RE = r"(?:about|around|approximately|approx\.?|nearly|almost|roughly|up\s+to|more\s+than|less\s+than|greater\s+than|lower\s+than|higher\s+than|au\s+moins|jusqu(?:'|’)?à|environ|presque|près\s+de|plus\s+de|moins\s+de)?"

V23_GENERIC_NUMERIC_VALUE_PATTERNS = [
    # Pourcentages avec qualificatifs : almost 95 %, up to 9.14%, more than 42 %
    rf"\b{V23_APPROX_WORDS_RE}\s*[-+]?\d+(?:[\.,]\d+)?\s*%",
    # Valeurs avec unités multi-domaine.
    rf"\b{V23_APPROX_WORDS_RE}\s*[-+]?\d+(?:[\.,]\d+)?\s*(?:°C|K|Pa|kPa|MPa|GPa|bar|N|kN|Hz|kHz|MHz|GHz|V|mV|A|mA|W|kW|J|kJ|mol|mmol|g|mg|kg|L|mL|µL|s|min|h|ms|µs|nm|µm|mm|cm|m|rpm|m/s|mm/s|fps|GFLOPS|TFLOPS)\b",
    # Scores / ratios / facteurs : 1.2x, 10-fold, x32, 3:1.
    r"\b[-+]?\d+(?:[\.,]\d+)?\s*(?:x|×|\-fold|fold)\b",
    r"\b(?:x|×)\s*[-+]?\d+(?:[\.,]\d+)?\b",
    r"\b\d+(?:[\.,]\d+)?\s*:\s*\d+(?:[\.,]\d+)?\b",
    # Ranges : 70-80 %, 1 to 5 mm, de 1 à 5.
    r"\b[-+]?\d+(?:[\.,]\d+)?\s*(?:-|–|—|to|à|a|and|et)\s*[-+]?\d+(?:[\.,]\d+)?\s*(?:%|°C|K|Pa|kPa|MPa|GPa|bar|N|kN|Hz|V|A|W|g|mg|kg|L|mL|s|min|h|nm|µm|mm|cm|m)?\b",
]

V23_METRIC_CONTEXT_PATTERNS = [
    # Mot métrique + qualificatif + valeur.
    rf"\b(?:accuracy|precision|recall|F1|F-score|AUC|mAP|RMSE|MAE|MSE|R2|R\^2|error|erreur|loss|gain|yield|rendement|conversion|efficiency|efficacité|resistance|résistance|strength|stiffness|modulus|conductivity|permeability|porosity|density|viscosity|hardness|durability|lifetime|latency|throughput|runtime|time|cost|coût|convergence|tolerance|tolérance)\b(?:\s+\w+){{0,6}}\s*(?:of|=|:|à|a|is|was|reaches?|reached|obtains?|obtained|atteint|obtenu)?\s*{V23_APPROX_WORDS_RE}\s*[-+]?\d+(?:[\.,]\d+)?\s*%?",
    # Valeur + mot résultat proche.
    rf"{V23_APPROX_WORDS_RE}\s*[-+]?\d+(?:[\.,]\d+)?\s*%?\s*(?:increase|decrease|reduction|improvement|gain|loss|drop|accuracy|precision|error|yield|rendement|efficiency|strength|resistance|résistance|augmentation|réduction|amélioration|baisse|hausse)",
]


def _v23_findall_unique_text(text: Any, patterns: List[str], max_items: int = 30) -> List[str]:
    out: List[str] = []
    seen = set()
    src = clean_text(text)
    for pat in patterns:
        try:
            for m in re.finditer(pat, src, flags=re.IGNORECASE):
                val = clean_text(m.group(0))
                val = re.sub(r"\s+", " ", val).strip(" ,;:.")
                if not val:
                    continue
                key = normalize_for_match(val)
                if key in seen:
                    continue
                seen.add(key)
                out.append(val)
                if len(out) >= max_items:
                    return out
        except Exception:
            continue
    return out


def _v23_extract_any_numeric_or_metric_mentions(sentence: Any) -> Dict[str, Any]:
    sent = clean_text(sentence)
    metric_context = _v23_findall_unique_text(sent, V23_METRIC_CONTEXT_PATTERNS, max_items=20)
    generic_values = _v23_findall_unique_text(sent, V23_GENERIC_NUMERIC_VALUE_PATTERNS, max_items=30)

    # Ancienne extraction stricte conservée.
    strict_metrics = _v20_extract_metrics(sent)
    explicit_numbers = _v21_extract_explicit_numeric_expressions(sent, max_items=25)
    equations = _v21_extract_equations_or_formulae(sent, max_items=10)
    variables = _v21_extract_parameter_names(sent, max_items=15)

    raw_values = unique_clean_list(metric_context + generic_values + explicit_numbers)

    status = "quantitative_metric_detected" if (strict_metrics or metric_context or raw_values) else "no_explicit_metric_value_detected"

    return {
        "metrics_detected": strict_metrics,
        "metric_context_mentions": metric_context,
        "raw_numeric_or_value_mentions": raw_values,
        "explicit_numeric_expressions": explicit_numbers,
        "equations_or_formulae": equations,
        "named_variables_or_parameters": variables,
        "metric_extraction_status": status,
        "no_metric_loss_rule": (
            "Si metrics_detected est vide, vérifier raw_numeric_or_value_mentions et result_text. "
            "Un résultat qualitatif ou une métrique non reconnue est conservé au lieu d'être supprimé."
        ),
    }


def _v22_extract_result_metric_values(sentence: str) -> Dict[str, Any]:
    """
    Override V2.3 de la fonction V2.2.
    Conserve l'ancien champ metrics_detected mais ajoute :
    - metric_context_mentions
    - raw_numeric_or_value_mentions
    - metric_extraction_status
    """
    return _v23_extract_any_numeric_or_metric_mentions(sentence)


def _v22_is_result_sentence(sentence: str) -> bool:
    """
    Override V2.3 :
    - garde les résultats avec métrique stricte ;
    - garde les phrases de résultat avec valeur brute même si la métrique est non reconnue ;
    - garde certains résultats qualitatifs forts, mais ils seront marqués sans métrique explicite.
    """
    if not clean_text(sentence):
        return False

    metrics = _v23_extract_any_numeric_or_metric_mentions(sentence)
    has_metric_or_value = (
        bool(metrics.get("metrics_detected"))
        or bool(metrics.get("metric_context_mentions"))
        or bool(metrics.get("raw_numeric_or_value_mentions"))
        or bool(metrics.get("explicit_numeric_expressions"))
    )
    has_number_or_symbol = _v21_has_numeric_or_symbolic_detail(sentence)
    has_result_signal = _v22_contains_any(sentence, V22_RESULT_SIGNAL_TERMS)

    # Résultat quantitatif.
    if has_result_signal and (has_metric_or_value or has_number_or_symbol):
        return True

    # Métrique très explicite même sans mot résultat.
    if has_metric_or_value and _v22_contains_any(sentence, ["accuracy", "precision", "error", "rmse", "mae", "yield", "rendement", "strength", "resistance", "résistance", "latency", "runtime"]):
        return True

    # Résultat qualitatif fort conservé comme no-loss, mais Phase 5 devra rester prudente.
    strong_qualitative = _v22_contains_any(sentence, [
        "outperform", "outperforms", "outperformed", "superior", "better", "worse",
        "significant", "significantly", "effective", "efficace", "améliore", "ameliore",
        "degrade", "degrades", "degraded", "dégrade", "reliable", "robust"
    ])
    return bool(has_result_signal and strong_qualitative)


def _v22_extract_result_test_method_links(source_text: str, profile: Dict[str, Any], max_results: int = 22) -> Dict[str, Any]:
    """
    Override V2.3 :
    - garde les résultats quantitatifs et qualitatifs ;
    - signale clairement quand la métrique est vide ;
    - lie chaque résultat à méthode/test quand possible ;
    - ajoute un résumé de couverture pour audit.
    """
    indexed = _v22_sentence_index(source_text)
    result_claims: List[Dict[str, Any]] = []
    seen = set()

    for sent_obj in indexed:
        sent = clean_text(sent_obj.get("text"))
        if not _v22_is_result_sentence(sent):
            continue

        key = normalize_for_match(sent)[:220]
        if key in seen:
            continue
        seen.add(key)

        idx = int(sent_obj.get("index", 0))
        metrics = _v22_extract_result_metric_values(sent)
        method_context = _v22_best_context(indexed, idx, sent, V22_METHOD_TECH_SIGNAL_TERMS, max_items=2)
        test_context = _v22_best_context(indexed, idx, sent, V22_TEST_PROTOCOL_SIGNAL_TERMS, max_items=2)

        method_protocol = profile.get("method_and_experimental_protocol") or {}
        data_protocol = profile.get("data_and_protocol") or {}
        architecture = profile.get("architecture") or {}
        implementation = profile.get("implementation_parameters") or {}
        open_domain = profile.get("open_domain_technical_evidence") or {}

        if not method_context:
            method_context = unique_clean_list(
                (method_protocol.get("method_or_process_snippets") or [])[:1]
                + (architecture.get("architecture_or_system_snippets") or [])[:1]
                + (implementation.get("software_solver_or_numerical_setup") or [])[:1]
                + (open_domain.get("unclassified_technical_snippets") or [])[:1]
            )[:2]

        if not test_context:
            test_context = unique_clean_list(
                (data_protocol.get("validation_or_test_protocol_snippets") or [])[:1]
                + (data_protocol.get("dataset_or_sample_snippets") or [])[:1]
                + (method_protocol.get("controls_baselines_references") or [])[:1]
                + (open_domain.get("unclassified_technical_snippets") or [])[:1]
            )[:2]

        metric_status = metrics.get("metric_extraction_status") or "unknown"
        confidence_score = 0
        if method_context:
            confidence_score += 1
        if test_context:
            confidence_score += 1
        if metric_status == "quantitative_metric_detected":
            confidence_score += 1

        if confidence_score >= 3:
            link_confidence = "high"
        elif confidence_score == 2:
            link_confidence = "medium"
        else:
            link_confidence = "low"

        result_claims.append({
            "result_id": f"R{len(result_claims)+1}",
            "result_text": truncate(sent, 620),
            "result_type": _v22_result_type(sent),
            "metrics_and_values": metrics,
            "linked_method_or_technology_context": method_context,
            "linked_test_or_validation_context": test_context,
            "evidence_window": {
                "previous_sentence": truncate((indexed[idx-1].get("text") if idx > 0 else ""), 420),
                "next_sentence": truncate((indexed[idx+1].get("text") if idx + 1 < len(indexed) else ""), 420),
            },
            "link_confidence": link_confidence,
            "metric_status": metric_status,
            "phase_5_instruction": (
                "Ne pas citer ce résultat seul. Le rattacher explicitement à la méthode/technologie, au test/protocole "
                "et aux données. Si metric_status=no_explicit_metric_value_detected, garder le résultat comme qualitatif "
                "ou signaler que la métrique exacte n'est pas explicitement détectée."
            ),
            "no_value_invented": True,
            "no_result_discarded_because_metric_empty": True,
        })

        if len(result_claims) >= max_results:
            break

    by_type: Dict[str, int] = {}
    metric_status_count: Dict[str, int] = {}
    for r in result_claims:
        by_type[r["result_type"]] = by_type.get(r["result_type"], 0) + 1
        st = r.get("metric_status") or "unknown"
        metric_status_count[st] = metric_status_count.get(st, 0) + 1

    return {
        "profile_type": "phase_4_5_v2_3_result_method_test_linking_no_metric_loss",
        "has_linked_results": bool(result_claims),
        "result_claims": result_claims,
        "result_count": len(result_claims),
        "result_types_count": by_type,
        "metric_status_count": metric_status_count,
        "no_result_discarded_because_domain_unknown": True,
        "no_result_discarded_because_metric_empty": True,
        "domain_specific_hardcoding": False,
        "phase_5_instruction": (
            "Utiliser ces result_claims pour enrichir l'état de l'art : chaque résultat doit être expliqué avec "
            "son test/protocole, sa méthode/technologie, ses données et sa métrique lorsque celle-ci est détectée. "
            "Si la métrique n'est pas détectée, utiliser result_text + evidence_window de manière prudente au lieu de supprimer l'information."
        ),
    }


_v22_build_technical_detail_profile = build_technical_detail_profile

def build_technical_detail_profile(card: Dict[str, Any], technical: Dict[str, Any]) -> Dict[str, Any]:
    """
    V2.3 — même base que V2.2, mais no-loss sur les résultats dont metrics_and_values était vide.
    """
    profile = _v21_build_technical_detail_profile(card, technical)
    source_text = build_technical_detail_source_text(card, technical)
    source_text = truncate(source_text, OPEN_DOMAIN_MAX_SOURCE_CHARS)
    result_links = _v22_extract_result_test_method_links(source_text, profile)
    profile["result_method_test_links"] = result_links
    profile["profile_type"] = "phase_4_5_v2_3_open_domain_result_method_test_no_metric_loss"
    profile["phase_5_instruction"] = (
        profile.get("phase_5_instruction", "")
        + " Utiliser result_method_test_links. Ne pas supprimer un résultat uniquement parce que metrics_detected est vide ; "
          "regarder raw_numeric_or_value_mentions, result_text et evidence_window."
    ).strip()
    if result_links.get("has_linked_results"):
        profile["has_any_detail"] = True
        profile["detail_score"] = min(100, int(profile.get("detail_score") or 0) + min(24, result_links.get("result_count", 0) * 3))
    return profile


_v22_summarize_technical_detail_profile = summarize_technical_detail_profile

def summarize_technical_detail_profile(profile: Dict[str, Any], *, max_chars: int = 1600) -> str:
    base = ""
    try:
        base = _v22_summarize_technical_detail_profile(profile, max_chars=max_chars)
    except Exception:
        base = ""

    result_links = (profile or {}).get("result_method_test_links") or {}
    claims = result_links.get("result_claims") or []
    parts = [base] if base else []
    if claims:
        rendered = []
        for r in claims[:4]:
            mav = r.get("metrics_and_values") or {}
            metric_bits = []
            for _, vals in (mav.get("metrics_detected") or {}).items():
                metric_bits.extend(vals[:2])
            metric_bits.extend((mav.get("metric_context_mentions") or [])[:2])
            metric_bits.extend((mav.get("raw_numeric_or_value_mentions") or [])[:2])
            if not metric_bits and mav.get("metric_extraction_status") == "no_explicit_metric_value_detected":
                metric_bits.append("métrique exacte non détectée ; résultat conservé")
            method_ctx = "; ".join((r.get("linked_method_or_technology_context") or [])[:1])
            test_ctx = "; ".join((r.get("linked_test_or_validation_context") or [])[:1])
            item = f"{r.get('result_id')}: {r.get('result_type')}"
            if metric_bits:
                item += " | valeur/métrique=" + ", ".join(unique_clean_list(metric_bits)[:3])
            if method_ctx:
                item += " | méthode/techno=" + truncate(method_ctx, 170)
            if test_ctx:
                item += " | test/protocole=" + truncate(test_ctx, 170)
            rendered.append(item)
        parts.append("résultats reliés méthode-test V2.3: " + " || ".join(rendered))
    return truncate(" ; ".join([p for p in parts if clean_text(p)]), max_chars)
