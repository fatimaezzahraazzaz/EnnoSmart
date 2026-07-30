# -*- coding: utf-8 -*-
from __future__ import annotations

"""
paper_ranker.py — EnnoScholar V146 core-concept gated ranking

Ranker déterministe pour classer les articles candidats :
- déduplication stable ;
- score explicable par chevauchement entre le verrou scientifique et l'article ;
- tags Direct / Connexe / Fondamental ;
- compatibilité avec le reranker BGE séparé dans paper_reranker_model.py.

Important :
Ce fichier ne charge aucun modèle lourd. Le BGE est uniquement dans paper_reranker_model.py.
"""

import math
import re
from typing import Any, Dict, List, Set, Tuple

from .utils import clean_text, norm, tokenize, token_set


STOP_RANK_TERMS = {
    "study", "paper", "article", "method", "methods", "model", "models",
    "system", "systems", "approach", "analysis", "using", "based",
    "result", "results", "performance", "evaluation", "validation",
    "research", "propose", "proposed", "framework",
    "projet", "verrou", "incertitude", "methode", "méthode",
    "modele", "modèle", "systeme", "système", "resultat", "résultat",
    "analyse", "travaux", "article", "etude", "étude",
    "avec", "sans", "dans", "pour", "sur", "sous", "entre", "vers",
    "sont", "est", "etre", "être", "qui", "que", "dont", "mais", "plus", "moins",
    "comparison", "comparaison", "cir", "frascati", "nlp", "rag", "llm",
    "dossier", "consultant", "ennodiagnostic", "ennoscholar", "software", "logiciel",
}

DIRECT_TAG = "Direct"
CONNEXE_TAG = "Connexe"
FONDAMENTAL_TAG = "Fondamental"
HORS_SUJET_TAG = "Hors sujet"

ADMIN_ANCHORS = {
    "CIR", "RND", "RD", "R&D", "NLP", "RAG", "LLM", "IA", "AI", "API", "JSON",
    "PDF", "DOCX", "HTTP", "DB", "SQL",
}

WEAK_ANCHORS = {
    "comparison", "comparaison", "validation", "evaluation", "performance",
    "method", "methods", "methode", "méthode", "model", "models", "modele", "modèle",
    "system", "systems", "systeme", "système", "data", "données", "image", "signal",
    "result", "results", "résultat", "study", "paper", "article", "software", "logiciel",
}



def _safe_text(x: Any, max_chars: int = 3000) -> str:
    return clean_text(x, max_chars)


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v or "").strip()]
    return [str(value)] if str(value or "").strip() else []


def _paper_key(article: Dict[str, Any]) -> str:
    """Clé stable pour déduplication DOI > paper_id > title+year."""
    if not isinstance(article, dict):
        return ""

    doi = _safe_text(article.get("doi"), 220).lower()
    if doi:
        return "doi:" + doi

    paper_id = _safe_text(
        article.get("paper_id")
        or article.get("paperId")
        or article.get("id")
        or article.get("external_id"),
        260,
    ).lower()
    if paper_id:
        return "id:" + paper_id

    title = norm(article.get("title"))[:260]
    year = str(article.get("year") or "").strip()
    if title:
        return f"title:{title}:{year}"

    return ""


def dedupe_papers(papers: List[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
    """
    Supprime les doublons d'articles en conservant le premier.
    API publique utilisée par scholar_agent.py.
    """
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    for article in papers or []:
        if not isinstance(article, dict):
            continue

        key = _paper_key(article)
        if not key:
            # On garde quand même l'article s'il a un titre exploitable.
            title = _safe_text(article.get("title"), 260)
            if not title:
                continue
            key = "title:" + norm(title)

        if key in seen:
            continue

        seen.add(key)
        out.append(article)

    return out


def _article_text(article: Dict[str, Any]) -> str:
    fields = article.get("fields_of_study") or article.get("fieldsOfStudy") or []
    if isinstance(fields, list):
        fields_text = " ".join(map(str, fields))
    else:
        fields_text = str(fields or "")

    authors = article.get("authors") or []
    if isinstance(authors, list):
        authors_text = " ".join(map(str, authors[:8]))
    else:
        authors_text = str(authors or "")

    return " ".join([
        str(article.get("title") or ""),
        str(article.get("abstract") or article.get("tldr") or article.get("summary") or ""),
        str(article.get("venue") or ""),
        fields_text,
        authors_text,
    ])



def _intent_text(intent: Dict[str, Any]) -> str:
    """Texte local du verrou. Le diagnostic global n'entre plus dans le ranking."""
    parts: List[str] = []
    for key in [
        "verrou_title", "original_title", "scientific_problem",
        "technical_object", "phenomenon",
    ]:
        if intent.get(key):
            parts.append(str(intent.get(key)))
    for key in ["methods", "key_terms_fr", "key_terms_en", "strong_anchors"]:
        parts.extend(_as_list(intent.get(key)))
    source_basis = intent.get("source_basis") or {}
    if isinstance(source_basis, dict):
        for key in ["title", "source_text_excerpt", "context_relevant_excerpt"]:
            if source_basis.get(key):
                parts.append(str(source_basis.get(key)))
    return clean_text(" ".join(parts), 5000)

def _tokens_clean(text: Any) -> Set[str]:
    return {
        t for t in tokenize(text)
        if t and len(t) >= 3 and norm(t) not in STOP_RANK_TERMS
    }



def _extract_anchors(intent: Dict[str, Any]) -> List[str]:
    """Ancres spécifiques du verrou, avec exclusion du bruit administratif/générique."""
    local_text = _intent_text(intent)
    anchors: List[str] = []
    anchors.extend(_as_list(intent.get("strong_anchors")))

    for token in re.findall(r"\b[A-Z][A-Z0-9]{1,}(?:[-_/][A-Z0-9]+)?\b", local_text):
        if token.upper() not in ADMIN_ANCHORS:
            anchors.append(token)

    for key in ["key_terms_fr", "key_terms_en", "methods"]:
        for value in _as_list(intent.get(key)):
            nv = norm(value)
            toks = _tokens_clean(value)
            if not nv or nv in WEAK_ANCHORS or not toks:
                continue
            if len(toks) >= 2 or (len(toks) == 1 and len(next(iter(toks))) >= 5):
                anchors.append(clean_text(value, 90))

    for key in ["technical_object", "phenomenon", "verrou_title", "original_title"]:
        toks = [t for t in _tokens_clean(intent.get(key)) if t not in WEAK_ANCHORS]
        ordered = [t for t in tokenize(intent.get(key)) if norm(t) in toks]
        for size in [3, 2]:
            for i in range(max(0, len(ordered) - size + 1)):
                expr = " ".join(ordered[i:i + size])
                if len(expr) >= 8:
                    anchors.append(expr)

    out: List[str] = []
    seen: Set[str] = set()
    for value in anchors:
        value = clean_text(value, 90)
        nv = norm(value)
        toks = _tokens_clean(value)
        if not nv or nv in seen or not toks:
            continue
        if all(t in WEAK_ANCHORS for t in toks):
            continue
        seen.add(nv)
        out.append(value)
    return out[:20]

def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))



def _contains_phrase(text_norm: str, phrase: Any) -> bool:
    """Correspondance exacte de mot/expression, jamais une simple sous-chaîne."""
    p = norm(phrase)
    if not p or len(p) < 3:
        return False
    pattern = re.escape(p).replace(r"\ ", r"\s+")
    return bool(re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", " " + text_norm + " "))

def _year_score(year: Any) -> float:
    try:
        y = int(year)
    except Exception:
        return 0.0

    if y >= 2022:
        return 0.06
    if y >= 2018:
        return 0.045
    if y >= 2012:
        return 0.025
    return 0.0


def _citation_score(article: Dict[str, Any]) -> float:
    try:
        c = int(article.get("citation_count") or article.get("citationCount") or 0)
    except Exception:
        c = 0

    if c <= 0:
        return 0.0

    # Score logarithmique plafonné.
    return min(math.log10(c + 1) / 40.0, 0.08)



SAR_ATR_CONTEXT_TERMS = [
    "synthetic data", "synthetic", "simulation", "simulator", "simulated",
    "training data", "limited data", "limited training", "representative",
    "representativeness", "generalization", "domain gap", "domain adaptation",
    "simulation-to-real", "sim-to-real", "measured", "measurement", "mstar",
    "ray tracing", "scattering center", "scattering centres", "mocem", "salsa",
]

SAR_ATR_CORE_TERMS = [
    "atr", "automatic target recognition", "sar atr", "synthetic aperture radar",
    "sar", "radar", "mstar", "mocem", "salsa",
]

SAR_ATR_OFFTOPIC_TERMS = [
    "medical", "médical", "cochlea", "cochlee", "cochlée", "tongue", "langue",
    "ultrasound", "echographique", "échographique", "pneumonia", "fundus",
    "plant disease", "plante", "biometric", "biometr", "survival analysis",
    "survie", "uav", "drone", "ship signal", "gear", "biomedical",
    "chest x-ray", "retinal", "retina", "iris", "fingerprint", "face recognition",
]

GENERIC_ONLY_TERMS = {
    "data", "image", "images", "signal", "signals", "classification", "detection",
    "segmentation", "model", "models", "learning", "deep", "cnn", "neural",
    "synthetic", "donnees", "données", "image", "traitement", "vision",
}


# V143 — Termes méthodologiques utiles pour un état de l'art CIR.
# Ces articles ne sont pas toujours Directs par rapport au verrou, mais ils peuvent
# être Fondamentaux/Connexes : augmentation de données, validation de simulation,
# robustesse, généralisation, CPU/GPU, etc.
METHODOLOGICAL_VALIDATION_TERMS = [
    "validation", "verification", "v&v", "model validation", "simulation validation",
    "virtual validation", "predictive simulation", "uncertainty quantification",
    "uncertainty-aware", "robustness", "generalization", "domain generalization",
    "domain adaptation", "sim-to-real", "simulation-to-real", "benchmark",
]

METHODOLOGICAL_AUGMENTATION_TERMS = [
    "data augmentation", "augmentation", "synthetic data", "generative", "gan",
    "diffusion", "adversarial", "domain randomization", "few-shot", "limited training",
    "limited data", "image classification", "feature extraction", "computer vision",
]

COMPUTE_CPU_GPU_TERMS = [
    "cpu", "gpu", "cuda", "opencl", "parallel", "vectorization", "multi-gpu",
    "heterogeneous", "performance comparison", "runtime", "acceleration",
]


def _has_phrase(text_norm: str, terms: List[str]) -> bool:
    return any(norm(t) in text_norm for t in terms if norm(t))


def _is_sar_atr_intent(intent: Dict[str, Any]) -> bool:
    n = norm(_intent_text(intent))
    has_atr = " atr " in f" {n} " or "automatic target recognition" in n
    has_radar_context = any(t in n for t in ["sar", "synthetic aperture radar", "radar", "mocem", "salsa", "mstar"])
    return bool(has_atr and has_radar_context)


def _sar_atr_score(article: Dict[str, Any], intent: Dict[str, Any], matched_anchors: List[str], matched_title_anchors: List[str], overlap: float, anchors: List[str]) -> Dict[str, Any] | None:
    """
    V143 : profil SAR/ATR équilibré.

    Objectif :
    - garder les vrais hors sujet en bas ;
    - ne plus transformer automatiquement tous les articles méthodologiques
      en Hors sujet lorsqu'ils n'ont pas SAR/ATR/radar dans le titre ;
    - laisser le consultant décider. Un article Hors sujet gardé par le consultant
      pourra être exploité plus tard, mais avec alerte.

    Règle :
    Direct = ancrage fort SAR/ATR/MOCEM/Salsa/MSTAR.
    Connexe = SAR/radar/ATR partiel ou CPU/GPU lié au verrou CPU/GPU.
    Fondamental = méthode scientifique utile au contexte : augmentation,
    validation, incertitude, généralisation, simulation, CPU/GPU général.
    Hors sujet = domaine clairement éloigné sans utilité méthodologique détectée.
    """
    if not _is_sar_atr_intent(intent):
        return None

    text = _article_text(article)
    n = norm(text)
    title_n = norm(article.get("title"))
    intent_n = norm(_intent_text(intent))

    has_atr = " atr " in f" {n} " or "automatic target recognition" in n
    has_sar = " sar " in f" {n} " or "synthetic aperture radar" in n
    has_radar = " radar " in f" {n} " or has_sar
    has_project_tool = any(t in n for t in ["mocem", "salsa"])
    has_mstar = "mstar" in n
    has_context = _has_phrase(n, SAR_ATR_CONTEXT_TERMS)
    is_offtopic = _has_phrase(n, SAR_ATR_OFFTOPIC_TERMS)

    title_strong = (
        " atr " in f" {title_n} "
        or "sar atr" in title_n
        or "synthetic aperture radar" in title_n
        or "mocem" in title_n
        or "salsa" in title_n
        or "mstar" in title_n
    )

    has_validation_method = _has_phrase(n, METHODOLOGICAL_VALIDATION_TERMS)
    has_augmentation_method = _has_phrase(n, METHODOLOGICAL_AUGMENTATION_TERMS)
    has_compute_method = _has_phrase(n, COMPUTE_CPU_GPU_TERMS)
    intent_is_compute = _has_phrase(intent_n, COMPUTE_CPU_GPU_TERMS) or "salsa" in intent_n

    methodological_signal = bool(has_validation_method or has_augmentation_method or has_compute_method)
    core_signal_count = sum([
        bool(has_atr),
        bool(has_sar),
        bool(has_radar),
        bool(has_project_tool),
        bool(has_mstar),
    ])

    # 1) Domaine clairement éloigné : on garde Hors sujet sauf si l'article a une
    # ancre SAR/ATR/radar forte. Cela évite plant/medical/veterinary/etc.
    if is_offtopic and core_signal_count == 0:
        tag = HORS_SUJET_TAG
        score = 0.02
        reason = "Article marqué Hors sujet : domaine clairement éloigné du verrou SAR/ATR et aucune ancre SAR/ATR/radar détectée."

    # 2) Articles coeur projet.
    elif has_project_tool and (has_atr or has_sar or has_radar or has_mstar):
        tag = DIRECT_TAG
        score = 0.92
        reason = "Article Direct : présence de MOCEM/Salsa avec ancrage SAR/ATR/radar/MSTAR."
    elif has_atr and (has_sar or has_radar) and (has_context or title_strong):
        tag = DIRECT_TAG if title_strong else CONNEXE_TAG
        score = 0.80 if title_strong else 0.62
        reason = "Article aligné SAR/ATR : lien avec données, simulation, généralisation, mesures ou reconnaissance de cibles."
    elif has_atr and (has_sar or has_radar):
        tag = CONNEXE_TAG
        score = 0.50
        reason = "Article Connexe : il traite SAR/ATR, mais le lien exact avec le verrou doit être vérifié."
    elif has_sar or has_radar or has_mstar:
        tag = FONDAMENTAL_TAG
        score = 0.30
        reason = "Article Fondamental : contexte SAR/radar/MSTAR utile mais pas directement centré sur le verrou."

    # 3) Articles méthodologiques : ne pas les jeter automatiquement.
    elif has_compute_method and intent_is_compute:
        tag = CONNEXE_TAG
        score = 0.42
        reason = "Article Connexe méthodologique : CPU/GPU/performance utile pour le verrou d'implémentation ou d'exécution Salsa."
    elif has_compute_method:
        tag = FONDAMENTAL_TAG
        score = 0.22
        reason = "Article Fondamental méthodologique : CPU/GPU/performance utile en contexte, mais pas une preuve directe SAR/ATR."
    elif has_augmentation_method:
        tag = FONDAMENTAL_TAG
        score = 0.28
        reason = "Article Fondamental méthodologique : augmentation de données / données synthétiques / classification utile pour construire l'état de l'art."
    elif has_validation_method:
        tag = FONDAMENTAL_TAG
        score = 0.24
        reason = "Article Fondamental méthodologique : validation, robustesse, incertitude ou généralisation utile au cadrage scientifique."

    # 4) Pas d'ancre ni méthode utile : vrai hors sujet.
    else:
        tag = HORS_SUJET_TAG
        score = 0.01
        reason = "Article marqué Hors sujet : aucune ancre SAR/ATR/radar ni utilité méthodologique suffisante détectée."

    if tag == DIRECT_TAG:
        score += min(0.04, _year_score(article.get("year")))
        score += min(0.03, _citation_score(article))
    elif tag == CONNEXE_TAG:
        score += min(0.025, _year_score(article.get("year")))
        score += min(0.02, _citation_score(article))
    elif tag == FONDAMENTAL_TAG:
        score += min(0.015, _year_score(article.get("year")))
        score += min(0.015, _citation_score(article))

    score = round(max(0.0, min(score, 1.0)), 4)

    return {
        "relevance_score": score,
        "tag": tag,
        "reason": reason,
        "score_details": {
            "ranker_version": "v143_balanced_sar_atr_methodological_ranker",
            "strict_profile": "sar_atr_balanced",
            "has_atr": bool(has_atr),
            "has_sar": bool(has_sar),
            "has_radar": bool(has_radar),
            "has_project_tool_mocem_salsa": bool(has_project_tool),
            "has_mstar": bool(has_mstar),
            "has_context_synthetic_validation": bool(has_context),
            "has_validation_method": bool(has_validation_method),
            "has_augmentation_method": bool(has_augmentation_method),
            "has_compute_method": bool(has_compute_method),
            "intent_is_compute": bool(intent_is_compute),
            "methodological_signal": bool(methodological_signal),
            "offtopic_detected": bool(is_offtopic),
            "strong_core_count": int(core_signal_count),
            "overlap": round(overlap, 4),
            "matched_anchors": matched_anchors[:12],
            "matched_title_anchors": matched_title_anchors[:12],
            "anchors_count": len(anchors),
            "year_bonus": round(_year_score(article.get("year")), 4),
            "citation_bonus": round(_citation_score(article), 4),
        },
    }



def score_paper(article: Dict[str, Any], intent: Dict[str, Any]) -> Dict[str, Any]:
    """Score générique strict : Direct exige une vraie compatibilité objet + ancre."""
    article = article or {}
    intent = intent or {}

    title = _safe_text(article.get("title"), 320)
    paper_text = _article_text(article)
    paper_norm = norm(paper_text)
    title_norm = norm(title)

    intent_tokens = _tokens_clean(_intent_text(intent))
    paper_tokens = _tokens_clean(paper_text)
    title_tokens = _tokens_clean(title)
    object_tokens = _tokens_clean(
        " ".join([
            str(intent.get("technical_object") or ""),
            str(intent.get("phenomenon") or ""),
            " ".join(_as_list(intent.get("methods"))),
        ])
    )

    anchors = _extract_anchors(intent)
    matched_anchors = [a for a in anchors if _contains_phrase(paper_norm, a)]
    matched_title_anchors = [a for a in anchors if _contains_phrase(title_norm, a)]

    specific_matched = [
        a for a in matched_anchors
        if norm(a) not in WEAK_ANCHORS and (
            len(_tokens_clean(a)) >= 2
            or any(ch.isupper() for ch in str(a))
            or len(norm(a)) >= 5
        )
    ]
    specific_title_matched = [a for a in matched_title_anchors if a in specific_matched]

    overlap = _jaccard(intent_tokens, paper_tokens)
    title_overlap = _jaccard(intent_tokens, title_tokens)
    object_overlap_tokens = object_tokens & paper_tokens
    object_overlap_count = len(object_overlap_tokens)

    direct_eligible = bool(
        (specific_title_matched and object_overlap_count >= 1)
        or (len(specific_matched) >= 2 and object_overlap_count >= 2)
    )

    memory_bonus = 0.02 if article.get("memory_v2_prior") else 0.0
    score = (
        0.34 * min(overlap * 5.0, 1.0)
        + 0.22 * min(title_overlap * 4.0, 1.0)
        + 0.22 * min(len(specific_matched) / 3.0, 1.0)
        + 0.12 * min(object_overlap_count / 4.0, 1.0)
        + _year_score(article.get("year"))
        + _citation_score(article)
        + memory_bonus
    )
    score = max(0.0, min(score, 1.0))

    if direct_eligible and score >= 0.38:
        tag = DIRECT_TAG
    elif specific_matched and object_overlap_count >= 1 and score >= 0.22:
        tag = CONNEXE_TAG
    elif overlap >= 0.035 or object_overlap_count >= 1:
        tag = FONDAMENTAL_TAG
    else:
        tag = HORS_SUJET_TAG

    if tag == DIRECT_TAG:
        reason = "Article Direct : ancre spécifique exacte et compatibilité avec l’objet scientifique du verrou."
    elif tag == CONNEXE_TAG:
        reason = "Article Connexe : lien technique partiel avec le verrou, sans preuve suffisante pour le classer Direct."
    elif tag == FONDAMENTAL_TAG:
        reason = "Article Fondamental : proximité méthodologique ou conceptuelle générale."
    else:
        reason = "Article Hors sujet : aucune ancre spécifique exacte et compatibilité technique insuffisante."

    return {
        "relevance_score": round(score, 4),
        "tag": tag,
        "reason": reason,
        "score_details": {
            "ranker_version": "v145_strict_exact_anchor_ranker",
            "overlap": round(overlap, 4),
            "title_overlap": round(title_overlap, 4),
            "matched_anchors": matched_anchors[:12],
            "matched_title_anchors": matched_title_anchors[:12],
            "specific_matched_anchors": specific_matched[:12],
            "specific_title_anchors": specific_title_matched[:12],
            "specific_anchor_count": len(specific_matched),
            "object_overlap_count": object_overlap_count,
            "object_overlap_tokens": sorted(object_overlap_tokens)[:12],
            "direct_eligible": direct_eligible,
            "anchors_count": len(anchors),
            "intent_tokens_count": len(intent_tokens),
            "paper_tokens_count": len(paper_tokens),
            "year_bonus": round(_year_score(article.get("year")), 4),
            "citation_bonus": round(_citation_score(article), 4),
            "memory_v2_bonus": round(memory_bonus, 4),
        },
    }

def rank_papers_for_intent(
    papers: List[Dict[str, Any]] | None,
    intent: Dict[str, Any],
    top_n: int = 12,
) -> List[Dict[str, Any]]:
    """
    Classe les articles pour un verrou scientifique EnnoScholar.

    Étapes :
    1. déduplication ;
    2. scoring déterministe ;
    3. tri par tag + score + mémoire V2 + citations ;
    4. retour Top N.
    """
    clean = dedupe_papers(papers)
    ranked: List[Dict[str, Any]] = []

    for p in clean:
        if not isinstance(p, dict):
            continue

        x = dict(p)

        # Ne pas scorer comme article académique une source technique catalogue.
        if x.get("source") == "technical_catalog" or x.get("source_type") == "technical_reference":
            x.setdefault("tag", "Technique")
            x.setdefault("relevance_score", 0.0)
            x.setdefault("reason", "Source technique proposée séparément au consultant.")
            ranked.append(x)
            continue

        try:
            scored = score_paper(x, intent)
            x.update(scored)
        except Exception as exc:
            x.setdefault("relevance_score", 0.0)
            x.setdefault("tag", FONDAMENTAL_TAG)
            details = x.get("score_details") if isinstance(x.get("score_details"), dict) else {}
            details["ranker_error"] = repr(exc)
            x["score_details"] = details
            x.setdefault(
                "reason",
                "Article conservé avec un score faible car le scoring automatique a échoué.",
            )

        ranked.append(x)

    tag_order = {
        DIRECT_TAG: 3,
        CONNEXE_TAG: 2,
        FONDAMENTAL_TAG: 1,
        "Technique": 0,
        HORS_SUJET_TAG: -1,
    }

    def _sort_key(x: Dict[str, Any]) -> Tuple[Any, ...]:
        try:
            citations = int(x.get("citation_count") or x.get("citationCount") or 0)
        except Exception:
            citations = 0
        return (
            tag_order.get(x.get("tag"), 0),
            float(x.get("relevance_score") or 0.0),
            1 if x.get("memory_v2_prior") else 0,
            citations,
            int(x.get("year") or 0) if str(x.get("year") or "").isdigit() else 0,
        )

    ranked.sort(key=_sort_key, reverse=True)

    return ranked[:max(1, int(top_n or 12))]


# =============================================================================
# V146 — Direct exige un concept coeur + un support méthodologique/phénoménologique
# =============================================================================
_SCORE_PAPER_V145 = score_paper


_V146_METHOD_ALIASES = {
    "method of moments": ["method of moments", "mom", "méthode des moments", "methode des moments"],
    "multilevel fast multipole method": ["multilevel fast multipole method", "mlfmm", "mflmm", "fast multipole method"],
    "uniform theory of diffraction": ["uniform theory of diffraction", "utd", "tud", "théorie uniforme de la diffraction", "theorie uniforme de la diffraction"],
    "physical optics": ["physical optics", "optique physique", "po", "op"],
    "electromagnetic ray tracing": ["electromagnetic ray tracing", "ray tracing", "ray launching", "lancer de rayons", "lancer de rayon"],
    "finite-difference time-domain": ["finite-difference time-domain", "finite difference time domain", "fdtd"],
    "finite element method": ["finite element method", "finite-element method", "fem", "éléments finis", "elements finis"],
    "full-wave electromagnetic method": ["full-wave electromagnetic", "full wave electromagnetic", "full-wave method", "full wave method"],
    "scattering-centre model": ["scattering-centre model", "scattering center model", "scattering centre model"],
}

_V146_PHENOMENON_ALIASES = {
    "computational cost and memory requirements": ["computational cost", "memory requirements", "runtime", "computation time", "temps de calcul", "ressources computationnelles"],
    "accuracy-computational cost trade-off": ["accuracy", "precision", "précision", "trade-off", "compromise", "faster", "computational cost"],
    "omitted edge-diffraction phenomena": ["edge diffraction", "diffraction des arêtes", "diffraction des aretes"],
    "model-form error from omitted physical phenomena": ["omitted physical phenomena", "not modelled", "not modeled", "simplifying assumptions", "model-form error"],
    "validation against reference methods or measurements": ["validation", "benchmark", "reference method", "comparison with measurements", "validated against"],
    "limited sim-to-real generalization": [
        "sim-to-real", "simulation-to-real", "synthetic-to-real",
        "generalization to real", "generalisation to real", "domain gap",
        "domain adaptation", "domain generalization", "domain generalisation",
        "cross-domain transfer", "synthetic-to-measured",
    ],
    "synthetic-to-real distribution shift": [
        "domain shift", "dataset shift", "distribution shift",
        "synthetic-to-real gap", "synthetic-to-measured gap",
    ],
    "uncertain physical representativeness": [
        "representativeness", "representative of real", "physical representativeness",
        "synthetic and measured", "measured versus synthetic",
        "measured vs synthetic", "simulation versus measurement",
    ],
}

_V146_RADAR_CONTRADICTIONS = [
    "specific absorption rate", "w/kg", "human exposure", "tissue", "water container",
    "raman spectroscopy", "surface-enhanced raman", "sers", "plasmonic", "photocatalysis",
    "biosensor", "biomedical imaging", "survival analysis", "game theory", "technical debt",
    "text-symbol", "cognitive model", "gene expression", "genomic", "genome-wide",
    "yeast datasets", "biological datasets", "bioinformatics", "protein", "clinical",
    "medical", "retinal", "ultrasound", "photonic crystal",
]


def _v146_exact(text_norm: str, phrase: str) -> bool:
    p = norm(phrase)
    if not p:
        return False
    pattern = re.escape(p).replace(r"\ ", r"\s+")
    return bool(re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", text_norm))


def _v146_concept_hit(article_norm: str, concept: str, aliases: Dict[str, Any]) -> bool:
    candidates = aliases.get(concept) or [concept]
    for alias in candidates:
        a = norm(alias)
        if not a:
            continue
        if alias.upper() == "SER":
            if _v146_exact(article_norm, "ser") and any(x in article_norm for x in ["radar", "scattering", "feko", "target", "rcs"]):
                return True
            continue
        if alias.upper() == "SAR":
            if _v146_exact(article_norm, "sar") and any(x in article_norm for x in ["radar", "synthetic aperture", "atr", "mstar", "target recognition"]):
                return True
            continue
        if alias.upper() == "ATR":
            if _v146_exact(article_norm, "atr") and any(x in article_norm for x in ["target recognition", "radar", "sar", "mstar"]):
                return True
            continue
        if _v146_exact(article_norm, alias):
            return True
    return False


def _v146_role_hits(article: Dict[str, Any], intent: Dict[str, Any]) -> Dict[str, Any]:
    text = " " + norm(_article_text(article)) + " "
    title = " " + norm(article.get("title")) + " "
    aliases = intent.get("concept_aliases") if isinstance(intent.get("concept_aliases"), dict) else {}
    core = [str(x) for x in intent.get("core_concepts") or []]
    primary = [str(x) for x in intent.get("primary_core_concepts") or core[:2]]
    methods = [str(x) for x in intent.get("method_anchors") or intent.get("methods") or []]
    phenomena = [str(x) for x in intent.get("phenomenon_anchors") or []]
    tools = [str(x) for x in intent.get("project_tool_terms") or []]
    implementation = [str(x) for x in intent.get("implementation_terms") or []]

    core_hits = [c for c in core if _v146_concept_hit(text, c, aliases)]
    core_title_hits = [c for c in core if _v146_concept_hit(title, c, aliases)]
    primary_hits = [c for c in primary if _v146_concept_hit(text, c, aliases)]
    primary_title_hits = [c for c in primary if _v146_concept_hit(title, c, aliases)]
    method_hits = [
        m for m in methods
        if any(_v146_exact(text, alias) for alias in _V146_METHOD_ALIASES.get(m, [m]))
    ]
    phenomenon_hits = [
        p for p in phenomena
        if any(_v146_exact(text, alias) for alias in _V146_PHENOMENON_ALIASES.get(p, [p]))
    ]
    tool_hits = [t for t in tools if _v146_exact(text, t)]
    implementation_hits = [t for t in implementation if _v146_exact(text, t)]

    radar_intent = any(c in core for c in ["radar cross section", "synthetic aperture radar", "automatic target recognition", "electromagnetic scattering"])
    contradictions = [x for x in _V146_RADAR_CONTRADICTIONS if radar_intent and _v146_exact(text, x)]

    return {
        "core_concept_hits": core_hits,
        "core_title_hits": core_title_hits,
        "primary_core_hits": primary_hits,
        "primary_title_hits": primary_title_hits,
        "method_anchor_hits": method_hits,
        "phenomenon_anchor_hits": phenomenon_hits,
        "project_tool_hits": tool_hits,
        "implementation_hits": implementation_hits,
        "domain_contradictions": contradictions,
    }


def score_paper(article: Dict[str, Any], intent: Dict[str, Any]) -> Dict[str, Any]:
    base = _SCORE_PAPER_V145(article, intent)
    role_fields_present = any(
        _as_list(intent.get(key))
        for key in [
            "core_concepts",
            "primary_core_concepts",
            "method_anchors",
            "phenomenon_anchors",
        ]
    )
    if not role_fields_present:
        details = dict(base.get("score_details") or {})
        details["domain_specific_ontology_used"] = False
        base["score_details"] = details
        return base

    roles = _v146_role_hits(article, intent)
    core_n = len(roles["core_concept_hits"])
    core_title_n = len(roles["core_title_hits"])
    primary_n = len(roles["primary_core_hits"])
    primary_title_n = len(roles["primary_title_hits"])
    method_n = len(roles["method_anchor_hits"])
    phen_n = len(roles["phenomenon_anchor_hits"])
    tool_n = len(roles["project_tool_hits"])
    impl_n = len(roles["implementation_hits"])
    contradiction = bool(roles["domain_contradictions"])
    support_n = method_n + phen_n
    primary_concepts = [str(x) for x in intent.get("primary_core_concepts") or []]
    primary_norm = {norm(x) for x in primary_concepts}
    secondary_core_hits = [
        concept for concept in roles["core_concept_hits"]
        if norm(concept) not in primary_norm
    ]
    independent_method_hits = [
        method for method in roles["method_anchor_hits"]
        if not any(
            norm(method) == norm(primary)
            or norm(method) in norm(primary)
            or norm(primary) in norm(method)
            for primary in primary_concepts
        )
    ]
    secondary_total = len([
        concept for concept in (intent.get("core_concepts") or [])
        if norm(concept) not in primary_norm
    ])
    primary_required = max(
        1,
        min(2, len(primary_concepts or roles["primary_core_hits"] or [1])),
    )
    secondary_required = 1 if secondary_total <= 1 else 2
    problem_evidence = bool(
        phen_n >= 1
        or len(independent_method_hits) >= 1
        or (
            secondary_total >= 1
            and len(secondary_core_hits) >= secondary_required
        )
    )
    direct_gate = bool(
        not contradiction
        and primary_n >= primary_required
        and primary_title_n >= 1
        and problem_evidence
    )

    old_score = float(base.get("relevance_score") or 0.0)
    year_bonus = min(_year_score(article.get("year")), 0.04)
    citation_bonus = min(_citation_score(article), 0.04)

    if contradiction:
        tag = HORS_SUJET_TAG
        score = 0.01
        reason = "Article Hors sujet : un sens scientifique contradictoire a été détecté malgré des acronymes ou méthodes communes."
    elif direct_gate:
        tag = DIRECT_TAG
        score = (
            0.72
            + min(primary_n * 0.035, 0.07)
            + min(len(secondary_core_hits) * 0.025, 0.05)
            + min((phen_n + len(independent_method_hits)) * 0.03, 0.06)
        )
        reason = (
            "Article Direct : l'objet scientifique principal et une preuve "
            "indépendante du problème du verrou sont tous deux couverts."
        )
    elif primary_n >= primary_required:
        tag = CONNEXE_TAG
        score = 0.50 + min(primary_n * 0.035, 0.07) + min(
            (len(secondary_core_hits) + phen_n + len(independent_method_hits)) * 0.02,
            0.06,
        )
        reason = (
            "Article Connexe : l'objet scientifique est bien couvert, mais "
            "l'article ne démontre pas encore le problème précis du verrou."
        )
    elif primary_n >= 1:
        tag = CONNEXE_TAG
        score = 0.40 + min(primary_n * 0.04, 0.08) + min(
            (len(secondary_core_hits) + phen_n + len(independent_method_hits)) * 0.02,
            0.04,
        )
        reason = (
            "Article Connexe : couverture partielle de l'objet principal, "
            "sans preuve complète du verrou."
        )
    elif core_n >= 1:
        tag = FONDAMENTAL_TAG
        score = 0.20 + min(core_n * 0.025, 0.06)
        reason = "Article Fondamental : proximité sur un concept secondaire, sans objet scientifique primaire commun."
    elif method_n >= 2 and phen_n >= 1:
        tag = CONNEXE_TAG
        score = 0.34
        reason = "Article Connexe méthodologique : méthodes et compromis comparables, mais objet scientifique différent."
    elif method_n >= 1 or phen_n >= 1:
        tag = FONDAMENTAL_TAG
        score = 0.18 + min((method_n + phen_n) * 0.025, 0.08)
        reason = "Article Fondamental : utilité méthodologique générale sans concept coeur commun démontré."
    elif impl_n or tool_n:
        tag = HORS_SUJET_TAG
        score = 0.02
        reason = "Article Hors sujet : proximité limitée à un outil ou à des détails CPU/GPU, sans lien scientifique coeur."
    else:
        tag = HORS_SUJET_TAG
        score = 0.005
        reason = "Article Hors sujet : aucun concept coeur ni support méthodologique suffisamment spécifique."

    score = round(max(0.0, min(score + year_bonus + citation_bonus, 1.0)), 4)
    details = dict(base.get("score_details") or {})
    details.update({
        "ranker_version": "v148_problem_evidence_gate",
        "core_concept_hits": roles["core_concept_hits"],
        "core_title_hits": roles["core_title_hits"],
        "method_anchor_hits": roles["method_anchor_hits"],
        "phenomenon_anchor_hits": roles["phenomenon_anchor_hits"],
        "project_tool_hits": roles["project_tool_hits"],
        "implementation_hits": roles["implementation_hits"],
        "domain_contradictions": roles["domain_contradictions"],
        "core_concept_hit_count": core_n,
        "core_title_hit_count": core_title_n,
        "primary_core_hits": roles["primary_core_hits"],
        "primary_title_hits": roles["primary_title_hits"],
        "primary_core_hit_count": primary_n,
        "primary_title_hit_count": primary_title_n,
        "method_anchor_hit_count": method_n,
        "phenomenon_anchor_hit_count": phen_n,
        "support_role_count": support_n,
        "secondary_core_hits": secondary_core_hits,
        "secondary_core_hit_count": len(secondary_core_hits),
        "secondary_core_required_for_direct": secondary_required if secondary_total else 0,
        "independent_method_hits": independent_method_hits,
        "independent_method_hit_count": len(independent_method_hits),
        "primary_required_for_direct": primary_required,
        "problem_evidence": problem_evidence,
        "domain_contradiction": contradiction,
        "direct_eligible": direct_gate,
        "specific_anchor_count": core_n + method_n + phen_n,
        "object_overlap_count": core_n,
        "relevance_score_v145": round(old_score, 4),
        "domain_specific_ontology_used": True,
    })
    return {"relevance_score": score, "tag": tag, "reason": reason, "score_details": details}


def rank_papers_for_intent(
    papers: List[Dict[str, Any]] | None,
    intent: Dict[str, Any],
    top_n: int = 12,
) -> List[Dict[str, Any]]:
    clean = dedupe_papers(papers)
    ranked: List[Dict[str, Any]] = []
    for paper in clean:
        if not isinstance(paper, dict):
            continue
        x = dict(paper)
        if x.get("source") == "technical_catalog" or x.get("source_type") == "technical_reference":
            x.setdefault("tag", "Technique")
            x.setdefault("relevance_score", 0.0)
            ranked.append(x)
            continue
        try:
            x.update(score_paper(x, intent))
        except Exception as exc:
            x["tag"] = HORS_SUJET_TAG
            x["relevance_score"] = 0.0
            x["reason"] = "Article non classé à cause d'une erreur du garde scientifique."
            x["score_details"] = {"ranker_version": "v148_problem_evidence_gate", "error": repr(exc)}
        ranked.append(x)

    order = {DIRECT_TAG: 4, CONNEXE_TAG: 3, FONDAMENTAL_TAG: 2, "Technique": 1, HORS_SUJET_TAG: 0}
    ranked.sort(key=lambda x: (
        order.get(str(x.get("tag") or ""), 0),
        float(x.get("relevance_score") or 0.0),
        int(x.get("citation_count") or 0),
        int(x.get("year") or 0) if str(x.get("year") or "").isdigit() else 0,
    ), reverse=True)
    return ranked[:max(1, int(top_n or 12))]
