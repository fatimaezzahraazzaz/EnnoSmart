# -*- coding: utf-8 -*-
from __future__ import annotations

"""
paper_ranker.py — EnnoScholar V133

Ranker multi-domaines :
- utilise le profil CIR issu de la nomenclature complète ;
- reconnaît les sources techniques reconnues ;
- évite de classer en "Fondamental" des articles clairement alignés au verrou ;
- reste compatible avec les tags UI : Direct / Connexe / Fondamental.
- V132 : Direct devient strict par profil : un article doit contenir les termes discriminants du verrou.
- V133 : le tag Direct est décidé uniquement à partir du titre/résumé/champs, jamais à partir de la requête qui a trouvé l'article ; critères Direct durcis.
"""

import math
import re
from typing import Any, Dict, List, Set

from .utils import clean_text, norm
from .cir_domain_query_catalog import get_cir_domain_profile, score_text_against_profile


STOP = {
    "the", "and", "or", "for", "with", "without", "under", "from", "into", "study", "review",
    "analysis", "experimental", "numerical", "model", "models", "modelling", "modeling",
    "method", "methods", "system", "systems", "effect", "effects", "performance",
    "engineering", "mechanical", "thermal", "pressure", "high", "low", "temperature",
    "water", "air", "flow", "rate", "control", "optimization", "optimal", "approach",
    "using", "based", "evaluation", "investigation", "investigations",
    "technical", "uncertainty", "service",
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


def paper_key(p: Dict[str, Any]) -> str:
    doi = clean_text(p.get("doi")).lower()
    if doi:
        return "doi:" + doi
    pid = clean_text(p.get("paper_id")).lower()
    if pid and not pid.startswith("tech:"):
        return "id:" + pid
    return "title:" + norm(p.get("title"))[:180] + ":" + str(p.get("year") or "")


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
        return 0.40
    if y >= 2020:
        return 1.0
    if y >= 2015:
        return 0.88
    if y >= 2010:
        return 0.75
    if y >= 2000:
        return 0.55
    if y >= 1990:
        return 0.35
    return 0.20


def _intent_text(intent: Dict[str, Any]) -> str:
    return _txt(
        intent.get("verrou_title"),
        intent.get("original_title"),
        intent.get("scientific_problem"),
        intent.get("technical_object"),
        intent.get("phenomenon"),
        " ".join(map(str, intent.get("constraints") or [])),
        " ".join(map(str, intent.get("methods") or [])),
        " ".join(map(str, intent.get("key_terms_en") or [])),
        " ".join(map(str, intent.get("key_terms_fr") or [])),
    )


def _domain_detection_from_intent(intent: Dict[str, Any]) -> Dict[str, Any]:
    for k in ["domain_detection", "cir_domain_detection"]:
        if isinstance(intent.get(k), dict):
            return intent.get(k) or {}
    return {}


def _get_profile(intent: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(intent.get("cir_domain_profile"), dict):
        return intent["cir_domain_profile"]
    return get_cir_domain_profile(_domain_detection_from_intent(intent), _intent_text(intent))


def _generic_overlap_score(paper: Dict[str, Any], intent: Dict[str, Any]) -> Dict[str, float]:
    intent_text = _intent_text(intent)
    paper_text = _txt(
        paper.get("title"),
        paper.get("abstract"),
        paper.get("tldr"),
        " ".join(paper.get("fields_of_study") or []),
        paper.get("venue"),
    )

    itoks = _words(intent_text)
    ptoks = _words(paper_text)
    title_toks = _words(paper.get("title"))

    overlap = len(itoks & ptoks) / max(4, min(len(itoks), 35)) if itoks and ptoks else 0.0
    title_overlap = len(itoks & title_toks) / max(3, min(len(itoks), 16)) if itoks and title_toks else 0.0

    phrase_hits = 0
    for phrase in (intent.get("key_terms_en") or [])[:14]:
        if len(str(phrase).split()) >= 2 and norm(phrase) in norm(paper_text):
            phrase_hits += 1

    return {
        "overlap": min(overlap, 1.0),
        "title_overlap": min(title_overlap, 1.0),
        "phrase_score": min(phrase_hits / 4.0, 1.0),
    }


def _query_match_score(paper: Dict[str, Any]) -> float:
    query = clean_text(paper.get("query"), 240)
    if not query or query == "technical_source_catalog":
        return 0.0

    q_words = _words(query)
    p_words = _words(_txt(paper.get("title"), paper.get("abstract")))
    if not q_words or not p_words:
        return 0.0

    return min(len(q_words & p_words) / max(3, min(len(q_words), 12)), 1.0)


def _domain_profile_score(paper: Dict[str, Any], intent: Dict[str, Any]) -> Dict[str, Any]:
    profile = _get_profile(intent)
    paper_text = _txt(
        paper.get("title"),
        paper.get("abstract"),
        paper.get("tldr"),
        paper.get("venue"),
        " ".join(paper.get("fields_of_study") or []),
    )
    scored = score_text_against_profile(paper_text, profile)
    score = float(scored.get("domain_profile_score") or 0.0)

    # V131 : les sources techniques sont affichées séparément ; elles ne doivent
    # pas être utilisées comme articles Direct/Connexe ni gonfler le score.
    if paper.get("source") == "technical_catalog":
        score = min(score, 0.20)

    # Heuristique anti-hors-sujet pour mots ambigus.
    n = norm(paper_text)
    if any(x in n for x in ["technical debt", "diphoton", "discord", "web service", "translation quality"]):
        if profile.get("profile_id") not in {"software_ai_data_cyber", "law_policy_regulation", "economics_management"}:
            score = min(score, 0.05)

    return {
        "profile_id": profile.get("profile_id"),
        "profile_label": profile.get("label"),
        "profile_score": round(max(0.0, min(score, 1.0)), 4),
        "matched_positive_terms": scored.get("matched_positive_terms") or [],
        "matched_negative_terms": scored.get("matched_negative_terms") or [],
    }



def _has_any(ntext: str, terms: List[str]) -> bool:
    return any(norm(t) in ntext for t in terms)


def _strict_profile_match(paper: Dict[str, Any], intent: Dict[str, Any]) -> Dict[str, Any]:
    """
    V132 : évite de classer Direct un article seulement parce qu'il contient
    des mots génériques comme building / wall / bio-based.

    direct_ready=True signifie que l'article contient les groupes de termes
    vraiment discriminants du profil du verrou.
    """
    profile = _get_profile(intent)
    pid = str(profile.get("profile_id") or "")
    # V133 IMPORTANT : on ne met PAS paper["query"] ici.
    # La requête peut contenir les bons mots-clés même si l'article ne les contient pas.
    # Le tag Direct doit donc être décidé uniquement depuis le titre, le résumé, le TLDR,
    # la venue et les champs disciplinaires de l'article.
    txt = _txt(
        paper.get("title"),
        paper.get("abstract"),
        paper.get("tldr"),
        paper.get("venue"),
        " ".join(paper.get("fields_of_study") or []),
    )
    n = norm(txt)
    title_n = norm(paper.get("title"))

    checks: List[str] = []
    direct_ready = False
    connexe_ready = False

    if pid == "bio_based_fire_resistance":
        # V133 : Direct feu plus exigeant.
        # Il faut une preuve feu dans le contenu de l'article, pas seulement dans la requête.
        fire_core = _has_any(n, [
            "fire resistance", "fire rating", "fire-resistance", "rei", "60 rei",
            "reaction to fire", "fire reaction", "charring", "char depth",
            "smouldering", "smoldering", "heat flux", "radiant heat",
            "compartment fire", "fire performance", "burning behaviour", "burning behavior",
        ])
        fire_title = _has_any(title_n, [
            "fire resistance", "fire safety", "reaction to fire", "fire reaction",
            "smouldering", "smoldering", "charring", "rei",
        ])
        assembly_wall = _has_any(n, [
            "wall assembly", "wall assemblies", "timber wall", "timber-framed wall",
            "timber frame wall", "panelised timber wall", "panelized timber wall",
            "building envelope", "envelope system", "façade", "facade", "infill", "insulation",
            "straw bale wall", "hempcrete wall", "bio-based insulation",
        ])
        bio_material = _has_any(n, [
            "bio-based", "biobased", "biosourced", "straw", "hemp", "hempcrete",
            "wood fibre", "wood fiber", "cellulose", "lignocellulosic", "timber", "wood",
        ])
        # Direct : article vraiment feu + système/paroi/isolation + matériau biosourcé.
        # Connexe : matériau biosourcé + feu, ou paroi/isolation biosourcée sans essai feu principal.
        checks = [
            "fire_core" if fire_core else "missing_fire_core",
            "fire_title" if fire_title else "fire_not_in_title",
            "assembly_wall" if assembly_wall else "missing_wall_or_insulation_system",
            "bio_material" if bio_material else "missing_bio_material",
        ]
        direct_ready = fire_core and assembly_wall and bio_material and (fire_title or _has_any(n, ["rei", "60 min", "60 minutes", "heat flux", "smouldering", "smoldering", "charring"]))
        connexe_ready = (fire_core and bio_material) or (assembly_wall and bio_material)

    elif pid == "bio_based_hygro_fungal_moisture":
        moisture = _has_any(n, [
            "hygrothermal", "moisture", "water vapour", "water vapor", "vapour",
            "vapor", "condensation", "humidity", "relative humidity", "sorption", "wufi",
        ])
        mould = _has_any(n, [
            "mould", "mold", "fungal", "fungi", "biological growth", "microbial",
            "mould growth", "mold growth", "fongique",
        ])
        bio_wall = _has_any(n, [
            "bio-based", "biobased", "straw", "hemp", "hempcrete", "timber frame",
            "wood-hemp", "wall", "insulation", "building envelope", "vapour-open", "vapor-open",
        ])
        checks = [
            "moisture" if moisture else "missing_moisture",
            "mould" if mould else "missing_mould",
            "bio_wall" if bio_wall else "missing_bio_wall",
        ]
        direct_ready = moisture and mould and bio_wall
        connexe_ready = (moisture and bio_wall) or (mould and bio_wall)

    elif pid == "bio_based_thermal_inertia":
        thermal_core = _has_any(n, [
            "thermal inertia", "thermal diffusivity", "thermal effusivity", "thermal mass",
            "phase shift", "decrement factor", "time lag", "lag time", "overheating", "summer comfort",
            "dynamic thermal", "thermal storage", "heat storage", "thermal comfort", "specific heat capacity",
        ])
        bio_wall = _has_any(n, [
            "bio-based", "biobased", "biosourced", "hemp", "hemp concrete", "hempcrete",
            "straw", "wood-hemp", "timber frame", "earth hemp", "raw earth", "wall", "building envelope",
            "envelope", "bio-based building", "natural fiber", "natural fibre",
        ])
        hygro_context = _has_any(n, ["hygrothermal", "moisture", "vapour", "vapor", "sorption", "condensation"])
        checks = [
            "thermal_core" if thermal_core else "missing_thermal_core",
            "bio_wall" if bio_wall else "missing_bio_wall",
            "hygro_context" if hygro_context else "no_hygro_context",
        ]
        # Direct seulement si le cœur thermique est réellement présent dans l'article.
        direct_ready = thermal_core and bio_wall
        connexe_ready = bio_wall and (thermal_core or hygro_context)

    elif pid == "timber_concrete_seismic_connectors":
        timber_conc = _has_any(n, [
            "timber concrete", "timber-concrete", "wood concrete", "wood-concrete",
            "clt-concrete", "clt concrete", "glulam concrete", "tcc", "timber–concrete",
            "cross-laminated timber-concrete", "laminated timber-concrete",
        ])
        connector = _has_any(n, [
            "connector", "connectors", "shear connector", "connection", "connections",
            "screw", "notch", "notched", "fastener", "coach screw", "goujon", "dowel",
            "push-out", "push out", "spline connection",
        ])
        load_behaviour = _has_any(n, [
            "seismic", "cyclic", "ductility", "diaphragm", "wind", "earthquake",
            "fatigue", "low-cycle", "reversed loading", "shear stiffness", "push-out", "push out",
            "displacement capacity", "ductility ratios", "in-plane shear",
        ])
        not_other_composite = not _has_any(n, [
            "steel-concrete", "steel concrete", "cold-formed steel", "electrical connector",
            "traffic", "discord", "software",
        ])
        checks = [
            "timber_concrete" if timber_conc else "missing_timber_concrete",
            "connector" if connector else "missing_connector",
            "load_behaviour" if load_behaviour else "missing_load_behaviour",
            "not_other_composite" if not_other_composite else "other_composite_or_offtopic",
        ]
        direct_ready = timber_conc and connector and load_behaviour and not_other_composite
        connexe_ready = not_other_composite and ((timber_conc and connector) or (connector and load_behaviour) or (timber_conc and load_behaviour))

    elif pid == "loose_fill_biobased_insulation_settlement":
        loose = _has_any(n, ["loose-fill", "loose fill", "blown insulation", "blown-in", "insufflation", "blown"])
        settlement = _has_any(n, ["settlement", "settling", "subsidence", "density", "compaction", "void", "cavity"])
        bio = _has_any(n, ["bio-based", "straw", "hemp", "hemp shiv", "cellulose", "wood fiber", "leaves"])
        checks = ["loose" if loose else "missing_loose", "settlement" if settlement else "missing_settlement", "bio" if bio else "missing_bio"]
        direct_ready = loose and settlement and bio
        connexe_ready = (loose and bio) or (settlement and bio)

    else:
        # profils génériques : on garde le comportement score/profil, mais Direct reste moins automatique.
        profile_score = float(_domain_profile_score(paper, intent).get("profile_score") or 0.0)
        title_overlap = _generic_overlap_score(paper, intent)["title_overlap"]
        direct_ready = profile_score >= 0.65 and title_overlap >= 0.16
        connexe_ready = profile_score >= 0.30
        checks = ["generic_profile"]

    return {
        "profile_id": pid,
        "direct_ready": bool(direct_ready),
        "connexe_ready": bool(connexe_ready),
        "strict_checks": checks,
    }

def _apply_tag(
    score: float,
    profile_score: float,
    title_overlap: float,
    source: str,
    strict: Dict[str, Any] | None = None,
) -> str:
    if source == "technical_catalog":
        return "Technique"

    strict = strict or {}
    direct_ready = bool(strict.get("direct_ready"))
    connexe_ready = bool(strict.get("connexe_ready"))

    # V132 : Direct uniquement si les termes discriminants du profil sont présents.
    if direct_ready and score >= 0.44 and profile_score >= 0.50:
        return "Direct"

    if connexe_ready and score >= 0.22:
        return "Connexe"

    if title_overlap >= 0.30 and score >= 0.30 and connexe_ready:
        return "Connexe"

    # Sans preuve stricte, un article reste au mieux Connexe, jamais Direct.
    if score >= 0.46 and connexe_ready and (profile_score >= 0.35 or title_overlap >= 0.22):
        return "Connexe"
    if score >= 0.25 and connexe_ready:
        return "Connexe"
    return "Fondamental"


def reason_for_tag(paper: Dict[str, Any], tag: str, matched: List[str] | None = None) -> str:
    title = clean_text(paper.get("title"), 180)
    year = paper.get("year")
    source_kind = paper.get("source_kind")
    extra = ""
    if matched:
        extra = " Termes alignés : " + ", ".join(matched[:6]) + "."
    if source_kind:
        extra = f" {source_kind}." + extra

    if tag == "Direct":
        return f"Source proche du verrou technique identifié : {title} ({year or 's.d.'}).{extra}"
    if tag == "Connexe":
        return f"Source connexe utile pour situer l’état de l’art : {title} ({year or 's.d.'}).{extra}"
    return f"Source de fond pouvant apporter un principe scientifique ou technique : {title} ({year or 's.d.'}).{extra}"


def score_paper(paper: Dict[str, Any], intent: Dict[str, Any]) -> Dict[str, Any]:
    base = _generic_overlap_score(paper, intent)
    profile = _domain_profile_score(paper, intent)
    query_score = _query_match_score(paper)

    citations = int(paper.get("citation_count") or 0)
    citation_score = min(math.log10(citations + 1) / 3.0, 1.0)
    recency = _recency_score(paper.get("year"))

    score = (
        0.24 * base["overlap"]
        + 0.20 * base["title_overlap"]
        + 0.10 * base["phrase_score"]
        + 0.36 * profile["profile_score"]
        + 0.02 * query_score  # V133 : la requête ne doit presque pas influencer le tag
        + 0.04 * citation_score
        + 0.04 * recency
    )

    # V131 : pas de boost score pour les sources techniques.

    # Si profil fort mais abstract court : remonter score.
    if profile["profile_score"] >= 0.55:
        score = max(score, 0.36 + 0.16 * profile["profile_score"])
    elif profile["profile_score"] >= 0.35:
        score = max(score, 0.24 + 0.14 * profile["profile_score"])

    # Pénalité hors-sujet explicite.
    if profile.get("matched_negative_terms"):
        score -= min(len(profile["matched_negative_terms"]) * 0.12, 0.35)

    score = max(0.0, min(score, 1.0))
    strict = _strict_profile_match(paper, intent)
    tag = _apply_tag(score, profile["profile_score"], base["title_overlap"], paper.get("source", ""), strict=strict)

    return {
        "relevance_score": round(score, 4),
        "tag": tag,
        "score_details": {
            "overlap": round(base["overlap"], 4),
            "title_overlap": round(base["title_overlap"], 4),
            "phrase_score": round(base["phrase_score"], 4),
            "domain_profile_id": profile.get("profile_id"),
            "domain_profile_label": profile.get("profile_label"),
            "profile_score": profile["profile_score"],
            "matched_positive_terms": profile["matched_positive_terms"],
            "matched_negative_terms": profile["matched_negative_terms"],
            "query_score": round(query_score, 4),
            "citation_score": round(citation_score, 4),
            "recency": round(recency, 4),
            "strict_direct_ready": strict.get("direct_ready"),
            "strict_connexe_ready": strict.get("connexe_ready"),
            "strict_checks": strict.get("strict_checks"),
        },
        "reason": reason_for_tag(paper, tag, profile["matched_positive_terms"]),
    }


def rank_papers_for_intent(papers: List[Dict[str, Any]], intent: Dict[str, Any], top_n: int = 12) -> List[Dict[str, Any]]:
    clean = dedupe_papers(papers)
    ranked = []
    for p in clean:
        x = dict(p)
        # ne pas écraser un tag technique déjà fourni, mais recalculer score quand même
        scored = score_paper(x, intent)
        x.update(scored)
        ranked.append(x)

    tag_order = {"Direct": 3, "Connexe": 2, "Fondamental": 1, "Technique": 0}
    ranked.sort(
        key=lambda x: (
            tag_order.get(x.get("tag"), 0),
            0 if x.get("source") == "technical_catalog" else 1,
            x.get("relevance_score", 0),
        ),
        reverse=True,
    )
    return ranked[:top_n]
