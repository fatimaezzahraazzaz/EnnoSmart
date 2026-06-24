# -*- coding: utf-8 -*-
from __future__ import annotations

"""
paper_ranker.py — EnnoScholar V3 profile-aware

Correction V32 :
Le backend enrichit maintenant les verrous, mais l'ancien ranker classait encore presque tout en
"Fondamental" parce qu'il utilisait seulement un overlap global trop sévère.

Cette version ajoute :
- scoring par profil technique du verrou ;
- boost sur les requêtes enrichies envoyées par le backend ;
- classification plus réaliste Direct / Connexe / Fondamental ;
- pénalités sur les articles clairement hors sujet.
"""

import math
import re
from typing import Any, Dict, List, Set

from .utils import clean_text, norm, token_set


STOP = {
    "the", "and", "or", "for", "with", "without", "under", "from", "into", "study", "review",
    "analysis", "experimental", "numerical", "model", "models", "modelling", "modeling",
    "method", "methods", "system", "systems", "effect", "effects", "performance",
    "engineering", "mechanical", "thermal", "pressure", "high", "low", "temperature",
    "water", "air", "flow", "rate", "control", "optimization", "optimal", "approach",
    "using", "based", "evaluation", "investigation", "investigations",
}


def _words(text: Any) -> Set[str]:
    out = set()
    for t in re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{2,}", str(text or "").lower()):
        t = t.strip("-")
        if t and t not in STOP:
            out.add(t)
    return out


def _txt(*values: Any) -> str:
    return " ".join(str(v or "") for v in values)


def _contains(text: str, *terms: str) -> bool:
    nt = norm(text)
    return any(norm(t) in nt for t in terms if t)


def _hit_count(text: str, terms: List[str]) -> int:
    nt = norm(text)
    return sum(1 for t in terms if norm(t) in nt)


def paper_key(p: Dict[str, Any]) -> str:
    doi = clean_text(p.get("doi")).lower()
    if doi:
        return "doi:" + doi
    return "title:" + norm(p.get("title"))[:160] + ":" + str(p.get("year") or "")


def dedupe_papers(papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = {}
    for p in papers:
        if not isinstance(p, dict) or p.get("normalized_error"):
            continue
        if not clean_text(p.get("title")):
            continue
        k = paper_key(p)
        if k not in seen:
            seen[k] = dict(p)
        else:
            prev = seen[k]
            sources = set(str(prev.get("source", "")).split("+"))
            sources.add(str(p.get("source", "")))
            prev["source"] = "+".join(sorted(x for x in sources if x))
            if not prev.get("abstract") and p.get("abstract"):
                prev["abstract"] = p["abstract"]
            prev["citation_count"] = max(int(prev.get("citation_count") or 0), int(p.get("citation_count") or 0))
            if not prev.get("url") and p.get("url"):
                prev["url"] = p.get("url")
            if len(clean_text(p.get("abstract"))) > len(clean_text(prev.get("abstract"))):
                prev["abstract"] = p.get("abstract")
    return list(seen.values())


def _recency_score(year: Any) -> float:
    try:
        y = int(year or 0)
    except Exception:
        return 0.25

    if y >= 2018:
        return 1.0
    if y >= 2010:
        return 0.75
    if y >= 2000:
        return 0.55
    if y >= 1990:
        return 0.35
    return 0.20


def _generic_overlap_score(paper: Dict[str, Any], intent: Dict[str, Any]) -> Dict[str, float]:
    intent_text = _txt(
        intent.get("scientific_problem"),
        intent.get("technical_object"),
        intent.get("phenomenon"),
        " ".join(intent.get("constraints") or []),
        " ".join(intent.get("methods") or []),
        " ".join(intent.get("key_terms_en") or []),
    )

    paper_text = _txt(
        paper.get("title"),
        paper.get("abstract"),
        paper.get("tldr"),
        " ".join(paper.get("fields_of_study") or []),
    )

    itoks = _words(intent_text)
    ptoks = _words(paper_text)
    title_toks = _words(paper.get("title"))

    overlap = len(itoks & ptoks) / max(4, min(len(itoks), 35)) if itoks and ptoks else 0.0
    title_overlap = len(itoks & title_toks) / max(3, min(len(itoks), 16)) if itoks and title_toks else 0.0

    phrase_hits = 0
    for phrase in (intent.get("key_terms_en") or [])[:10]:
        if len(str(phrase).split()) >= 2 and norm(phrase) in norm(paper_text):
            phrase_hits += 1

    return {
        "overlap": min(overlap, 1.0),
        "title_overlap": min(title_overlap, 1.0),
        "phrase_score": min(phrase_hits / 4.0, 1.0),
    }


def _query_match_score(paper: Dict[str, Any], intent: Dict[str, Any]) -> float:
    query = clean_text(paper.get("query"), 240)
    if not query:
        return 0.0

    q_words = _words(query)
    p_words = _words(_txt(paper.get("title"), paper.get("abstract")))
    if not q_words or not p_words:
        return 0.0

    return min(len(q_words & p_words) / max(3, min(len(q_words), 10)), 1.0)


def _profile(intent: Dict[str, Any]) -> str:
    return str(
        intent.get("backend_enrichment_profile")
        or intent.get("enrichment_profile")
        or intent.get("profile")
        or ""
    )


def _profile_score(paper: Dict[str, Any], intent: Dict[str, Any]) -> Dict[str, Any]:
    profile = _profile(intent)
    title = clean_text(paper.get("title"), 300)
    body = clean_text(_txt(paper.get("title"), paper.get("abstract"), paper.get("tldr"), " ".join(paper.get("fields_of_study") or [])), 5000)
    nt = norm(body)

    score = 0.0
    tag_hint = "Fondamental"
    matched = []

    def add(points: float, term: str):
        nonlocal score
        score += points
        matched.append(term)

    if profile == "legacy_domain_profile_disabled":
        if _contains(nt, "reciprocating compressor", "compressor"):
            add(0.20, "compressor")
        if _contains(nt, "technical evidence", "technical uncertainty", "technical limitation"):
            add(0.25, "technical terms")
        if _contains(nt, "blow-by", "blowby", "blow by", "leakage"):
            add(0.20, "blow-by/leakage")
        if _contains(nt, "crankcase", "crankcase pressure", "ventilation"):
            add(0.18, "crankcase")
        if _contains(nt, "wear", "friction", "degradation"):
            add(0.12, "wear/friction")
        if _contains(nt, "high-pressure", "high pressure", "oil-free compressor", "oil free compressor"):
            add(0.12, "high pressure/oil-free")
        if _contains(nt, "engine") and not _contains(nt, "compressor"):
            score -= 0.10

        if score >= 0.50:
            tag_hint = "Direct"
        elif score >= 0.28:
            tag_hint = "Connexe"

    elif profile == "thermal_cooling_intercooler":
        if _contains(nt, "compressor", "compressed air"):
            add(0.22, "compressor")
        if _contains(nt, "reciprocating compressor"):
            add(0.16, "reciprocating compressor")
        if _contains(nt, "intercooler", "heat exchanger", "cooler"):
            add(0.25, "intercooler/heat exchanger")
        if _contains(nt, "cooling", "thermal management", "heat transfer", "temperature"):
            add(0.20, "cooling/thermal")
        if _contains(nt, "water flow", "water cooling", "flow rate"):
            add(0.16, "water flow")
        if _contains(nt, "high pressure", "high-pressure", "300 bar"):
            add(0.12, "high pressure")
        if _contains(nt, "solar", "urban", "polyamide", "reactor", "methane", "liquefaction", "building"):
            score -= 0.18

        if score >= 0.52:
            tag_hint = "Direct"
        elif score >= 0.30:
            tag_hint = "Connexe"

    elif profile == "vibro_acoustic_compressor":
        if _contains(nt, "compressor", "reciprocating compressor"):
            add(0.25, "compressor")
        if _contains(nt, "vibration", "vibro", "dynamic"):
            add(0.22, "vibration")
        if _contains(nt, "acoustic", "noise", "sound"):
            add(0.22, "acoustic/noise")
        if _contains(nt, "suction", "pulsation", "intake"):
            add(0.18, "suction/pulsation")
        if _contains(nt, "high pressure"):
            add(0.08, "high pressure")
        if score >= 0.50:
            tag_hint = "Direct"
        elif score >= 0.28:
            tag_hint = "Connexe"

    elif profile == "compressed_air_drying":
        if _contains(nt, "compressed air", "compressor"):
            add(0.22, "compressed air/compressor")
        if _contains(nt, "drying", "dryer", "dew point", "moisture"):
            add(0.28, "drying/dew point")
        if _contains(nt, "condensate", "separator", "separation"):
            add(0.20, "condensate separation")
        if _contains(nt, "high pressure"):
            add(0.10, "high pressure")
        if score >= 0.50:
            tag_hint = "Direct"
        elif score >= 0.28:
            tag_hint = "Connexe"

    elif profile == "counterweight_dynamic_balancing":
        if _contains(nt, "counterweight", "balance weight"):
            add(0.25, "counterweight")
        if _contains(nt, "dynamic balancing", "balancing", "unbalance"):
            add(0.25, "balancing")
        if _contains(nt, "rotating machinery", "compressor", "shaft"):
            add(0.18, "rotating machinery")
        if _contains(nt, "vibration"):
            add(0.15, "vibration")
        if _contains(nt, "lead-free", "lead free", "lead replacement"):
            add(0.10, "lead-free")
        if score >= 0.50:
            tag_hint = "Direct"
        elif score >= 0.28:
            tag_hint = "Connexe"

    return {
        "profile_score": round(max(0.0, min(score, 1.0)), 4),
        "profile_tag_hint": tag_hint,
        "profile_matched_terms": matched[:8],
    }


def _apply_tag(score: float, profile_tag_hint: str) -> str:
    # Le tag profil est prioritaire s'il détecte une proximité technique forte.
    if profile_tag_hint == "Direct" and score >= 0.38:
        return "Direct"
    if profile_tag_hint == "Connexe" and score >= 0.24:
        return "Connexe"

    if score >= 0.52:
        return "Direct"
    if score >= 0.26:
        return "Connexe"
    return "Fondamental"


def score_paper(paper: Dict[str, Any], intent: Dict[str, Any]) -> Dict[str, Any]:
    base = _generic_overlap_score(paper, intent)
    profile = _profile_score(paper, intent)
    query_score = _query_match_score(paper, intent)

    citations = int(paper.get("citation_count") or 0)
    citation_score = min(math.log10(citations + 1) / 3.0, 1.0)
    recency = _recency_score(paper.get("year"))

    score = (
        0.28 * base["overlap"]
        + 0.20 * base["title_overlap"]
        + 0.10 * base["phrase_score"]
        + 0.26 * profile["profile_score"]
        + 0.10 * query_score
        + 0.03 * citation_score
        + 0.03 * recency
    )

    # Si le profil détecte un article réellement proche, éviter de le bloquer à cause du faible abstract.
    if profile["profile_tag_hint"] == "Direct":
        score = max(score, 0.44 + 0.18 * profile["profile_score"])
    elif profile["profile_tag_hint"] == "Connexe":
        score = max(score, 0.28 + 0.12 * profile["profile_score"])

    score = max(0.0, min(score, 1.0))
    tag = _apply_tag(score, profile["profile_tag_hint"])

    return {
        "relevance_score": round(score, 4),
        "tag": tag,
        "score_details": {
            "overlap": round(base["overlap"], 4),
            "title_overlap": round(base["title_overlap"], 4),
            "phrase_score": round(base["phrase_score"], 4),
            "profile_score": profile["profile_score"],
            "profile_tag_hint": profile["profile_tag_hint"],
            "profile_matched_terms": profile["profile_matched_terms"],
            "query_score": round(query_score, 4),
            "citation_score": round(citation_score, 4),
            "recency": round(recency, 4),
        },
        "reason": reason_for_tag(paper, tag, profile["profile_matched_terms"]),
    }


def reason_for_tag(paper: Dict[str, Any], tag: str, matched: List[str] | None = None) -> str:
    title = clean_text(paper.get("title"), 160)
    year = paper.get("year")
    extra = ""
    if matched:
        extra = " Termes alignés : " + ", ".join(matched[:5]) + "."
    if tag == "Direct":
        return f"Article proche du verrou technique identifié : {title} ({year}).{extra}"
    if tag == "Connexe":
        return f"Article connexe utile pour situer l’état de l’art : {title} ({year}).{extra}"
    return f"Article de fond pouvant apporter un principe scientifique ou technique : {title} ({year}).{extra}"


def rank_papers_for_intent(papers: List[Dict[str, Any]], intent: Dict[str, Any], top_n: int = 12) -> List[Dict[str, Any]]:
    clean = dedupe_papers(papers)
    ranked = []
    for p in clean:
        x = dict(p)
        x.update(score_paper(x, intent))
        ranked.append(x)

    # Garder d'abord les articles Direct/Connexe, puis les fondamentaux.
    tag_order = {"Direct": 2, "Connexe": 1, "Fondamental": 0}
    ranked.sort(key=lambda x: (tag_order.get(x.get("tag"), 0), x.get("relevance_score", 0)), reverse=True)
    return ranked[:top_n]
