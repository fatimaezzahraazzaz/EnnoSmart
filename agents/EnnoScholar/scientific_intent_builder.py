# -*- coding: utf-8 -*-
from __future__ import annotations

"""
scientific_intent_builder.py — EnnoScholar V2.1 source evidence first

Méthode V2.1 :
- Ne pas partir du thème Frascati générique.
- Partir d'abord des preuves techniques sources : raw_item.text, sources.excerpt, supporting_passages.
- Supprimer les phrases "Question de qualification".
- Le diagnostic reformulé est utilisé seulement après filtrage par proximité avec ces preuves.
"""

import re
from typing import Any, Dict, List, Tuple

from .models import ScientificIntent
from .utils import (
    clean_text,
    clean_title,
    dedupe_keep_order,
    flatten_text,
    jaccard,
    norm,
    remove_frascati_question_text,
    token_set,
    tokenize,
)

from .cir_domain_query_catalog import get_cir_domain_profile


TRANSLATIONS = {
    # général
    "verrou": "technical uncertainty",
    "incertitude": "uncertainty",
    "performance": "performance",
    "prototype": "prototype",
    "essai": "experiment",
    "essais": "experiments",
    "mesure": "measurement",
    "mesures": "measurements",
    "simulation": "simulation",
    "modèle": "model",
    "modele": "model",
    "optimisation": "optimization",
    "validation": "validation",
    "limite": "limitation",
    "méthode": "method",
    "methode": "method",

    # informatique
    "logiciel": "software",
    "algorithme": "algorithm",
    "intelligence artificielle": "artificial intelligence",
    "apprentissage automatique": "machine learning",
    "réseau neuronal": "neural network",
    "reseau neuronal": "neural network",
    "données": "data",
    "donnees": "data",
    "classification": "classification",
    "détection": "detection",
    "detection": "detection",
    "prédiction": "prediction",
    "prediction": "prediction",

    # mécanique
    "génie mécanique": "mechanical engineering",
    "genie mecanique": "mechanical engineering",
    "mécanique": "mechanical",
    "mecanique": "mechanical",
    "cylindre": "cylinder",
    "étanchéité": "sealing",
    "etancheite": "sealing",
    "fuite": "leakage",
    "usure": "wear",
    "frottement": "friction",
    "vibration": "vibration",
    "acoustique": "acoustic",
    "bruit": "noise",
    "aspiration": "suction",
    "thermique": "thermal",
    "refroidissement": "cooling",
    "température": "temperature",
    "temperature": "temperature",
    "pression": "pressure",
    "débit": "flow rate",
    "debit": "flow rate",

    # autres domaines
    "chimie": "chemistry",
    "chimique": "chemical",
    "matériaux": "materials",
    "materiaux": "materials",
    "polymère": "polymer",
    "polymere": "polymer",
    "alliage": "alloy",
    "surface": "surface",
    "revêtement": "coating",
    "revetement": "coating",
    "corrosion": "corrosion",
    "catalyse": "catalysis",
    "composite": "composite",
    "biologie": "biology",
    "cellule": "cell",
    "protéine": "protein",
    "proteine": "protein",
    "enzyme": "enzyme",
    "biomarqueur": "biomarker",
    "médical": "medical",
    "medical": "medical",
    "clinique": "clinical",
    "diagnostic": "diagnosis",
    "imagerie": "imaging",
    "électronique": "electronics",
    "electronique": "electronics",
    "capteur": "sensor",
    "signal": "signal",
    "embarqué": "embedded",
    "embarque": "embedded",
    "firmware": "firmware",
    "antenne": "antenna",
    "énergie": "energy",
    "energie": "energy",
    "batterie": "battery",
    "hydrogène": "hydrogen",
    "hydrogene": "hydrogen",
    "rendement": "efficiency",
    "recyclage": "recycling",
    "fermentation": "fermentation",
}

GENERIC_TITLE_TERMS = {
    "performance", "insuffisante", "contrainte", "comportement", "instable",
    "maitrise", "maîtrise", "qualite", "qualité", "sortie", "non", "conforme",
    "difficile", "garantir", "fiabilite", "fiabilité", "degradation", "dégradation",
    "fonctionnement",
}

METHOD_MARKERS = {
    "essai", "essais", "test", "tests", "experiment", "experimental",
    "simulation", "model", "modele", "modèle", "prototype", "mesure", "measurement",
    "validation", "optimization", "optimisation", "benchmark",
}

CONSTRAINT_MARKERS = {
    "haute", "high", "basse", "low", "pression", "pressure", "température", "temperature",
    "débit", "debit", "flow", "compact", "compacité", "compacite",
    "temps", "real-time", "réel", "precision", "précision", "robustesse", "robustness",
    "coût", "cost", "energie", "énergie", "energy",
}

PHENOMENON_MARKERS = {
    "uncertainty", "incertitude", "limite", "limitation", "failure", "defaillance",
    "défaillance", "performance", "bruit", "noise", "vibration", "usure", "wear",
    "fuite", "leakage", "thermal", "thermique", "cooling", "refroidissement",
    "erreur", "error", "accuracy", "precision", "robustesse", "robustness",
    "corrosion", "degradation", "dégradation", "detection", "détection",
    "sealing", "friction", "temperature",
}


# V128 — vocabulaire bâtiment / matériaux biosourcés.
# Important : ces traductions restent génériques, elles ne ciblent aucun projet particulier.
TRANSLATIONS.update({
    "matériaux biosourcés": "bio-based building materials",
    "materiaux biosources": "bio-based building materials",
    "biosourcé": "bio-based",
    "biosource": "bio-based",
    "bio-sourcé": "bio-based",
    "chanvre": "hemp",
    "chènevotte": "hemp shiv",
    "chenevotte": "hemp shiv",
    "paille": "straw",
    "paille hachée": "chopped straw",
    "paille hachee": "chopped straw",
    "insufflation": "blown insulation",
    "insufflé": "blown insulation",
    "insufflee": "blown insulation",
    "vrac": "loose-fill",
    "tassement": "settlement",
    "paroi": "wall",
    "parois": "walls",
    "façade": "facade",
    "facade": "facade",
    "ossature bois": "timber frame",
    "bois": "timber",
    "bois/béton": "timber concrete composite",
    "bois beton": "timber concrete composite",
    "béton": "concrete",
    "beton": "concrete",
    "connecteur": "connector",
    "connecteurs": "connectors",
    "goujon": "dowel connector",
    "goujons": "dowel connectors",
    "ductilité": "ductility",
    "ductilite": "ductility",
    "séisme": "seismic loading",
    "seisme": "seismic loading",
    "sismique": "seismic",
    "vent": "wind load",
    "diaphragme": "diaphragm",
    "feu": "fire resistance",
    "incendie": "fire resistance",
    "rei": "fire resistance rating",
    "hygrothermique": "hygrothermal",
    "hygrométrique": "moisture",
    "hygrometrique": "moisture",
    "humidité": "moisture",
    "humidite": "moisture",
    "fongique": "fungal growth",
    "moisissure": "mould growth",
    "moisissures": "mould growth",
    "perspirant": "vapour-open wall",
    "perspirante": "vapour-open wall",
    "diffusivité": "thermal diffusivity",
    "diffusivite": "thermal diffusivity",
    "effusivité": "thermal effusivity",
    "effusivite": "thermal effusivity",
    "déphasage": "thermal phase shift",
    "dephasage": "thermal phase shift",
    "inertie": "thermal inertia",
    "confort d’été": "summer comfort",
    "confort d'ete": "summer comfort",
    "heures d’inconfort": "overheating hours",
    "heures d'inconfort": "overheating hours",
})


def detect_enrichment_profile_from_text(*parts: Any) -> str:
    """Profil scientifique générique utilisé par EnnoScholar pour cadrer les requêtes.

    V129 corrige une confusion : un verrou hygro/fongique ne doit pas devenir
    automatiquement un verrou tassement seulement parce que le texte contient
    "insufflation" ou "chènevotte". Le tassement exige des marqueurs explicites
    comme tassement, settlement, densité, vide, compaction.
    """
    t = norm(" ".join(str(p or "") for p in parts))

    if any(x in t for x in ["connecteur", "connectors", "connector", "goujon", "goujons", "shear connector", "timber concrete", "bois beton", "bois/beton", "wood concrete"]):
        if any(x in t for x in ["seisme", "seismic", "earthquake", "ductil", "ductility", "vent", "wind", "diaphrag", "composite", "cyclic"]):
            return "timber_concrete_seismic_connectors"
        return "timber_concrete_connectors"

    if any(x in t for x in ["rei", "feu", "incendie", "fire", "resistance au feu", "fire resistance", "reaction to fire"]):
        return "bio_based_fire_resistance"

    # Thermique/inertie : priorité sur perspirant.
    if any(x in t for x in ["effusiv", "diffusiv", "dephas", "déphas", "inertie", "thermal inertia", "thermal mass", "summer comfort", "confort d ete", "confort ete", "overheating"]):
        return "bio_based_thermal_inertia"

    has_hygro = any(x in t for x in ["fongique", "fungal", "moisiss", "mould", "mold", "hygro", "humid", "moisture", "perspir", "vapour", "vapor", "condensation"])
    has_settlement = any(x in t for x in ["tassement", "settlement", "settling", "compaction", "compactage", "vide superieur", "air cavity", "cavities", "densite d insufflation", "density"])
    if has_hygro and not has_settlement:
        return "bio_based_hygro_fungal_moisture"
    if has_settlement:
        return "loose_fill_biobased_insulation_settlement"

    if any(x in t for x in ["insufflation", "insuffle", "insufflee", "vrac", "loose fill", "blown insulation", "chenevotte"]):
        return "loose_fill_biobased_insulation_settlement"

    if any(x in t for x in ["acoust", "vibrat", "vibration", "multi physique", "multiphysics"]):
        return "building_multiphysics_comfort"
    if any(x in t for x in ["biosource", "bio based", "bio-based", "chanvre", "hemp", "paille", "straw", "construction"]):
        return "bio_based_building_materials_general"
    return "generic"


def translate_terms(terms: List[str]) -> List[str]:
    out = []
    for term in terms:
        n = norm(term)
        matched = False
        for fr, en in sorted(TRANSLATIONS.items(), key=lambda kv: -len(kv[0])):
            nf = norm(fr)
            if nf == n or nf in n or n in nf:
                out.append(en)
                matched = True
                break
        if not matched:
            out.append(term)
    return dedupe_keep_order(out)


def extract_domain_terms(domain_detection: Dict[str, Any] | None) -> List[str]:
    if not isinstance(domain_detection, dict):
        return []

    candidates = [
        domain_detection.get("sub_domain_label"),
        domain_detection.get("domain_label_niv3"),
        domain_detection.get("main_domain_label"),
        domain_detection.get("domain_label_niv2"),
        domain_detection.get("display_label"),
        domain_detection.get("domain_key"),
    ]

    text = ""
    for c in candidates:
        if c:
            text = str(c)
            break

    if "→" in text:
        text = text.split("→")[-1].strip()
    elif ">" in text:
        text = text.split(">")[-1].strip()

    terms = extract_keyphrases(text, max_terms=5)
    return translate_terms(terms)[:3]


def extract_keyphrases(text: Any, max_terms: int = 18) -> List[str]:
    s = norm(remove_frascati_question_text(text))
    tokens = tokenize(s)

    phrases = []
    for fr in sorted(TRANSLATIONS.keys(), key=len, reverse=True):
        nf = norm(fr)
        if nf and nf in s:
            phrases.append(fr)

    ngrams = []
    for n in [3, 2]:
        for i in range(0, max(0, len(tokens) - n + 1)):
            ng = " ".join(tokens[i:i+n])
            if len(ng) >= 7:
                ngrams.append(ng)

    freq = {}
    for t in tokens:
        freq[t] = freq.get(t, 0) + 1

    ranked_tokens = [t for t, _ in sorted(freq.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))]

    return dedupe_keep_order(phrases + ngrams[:12] + ranked_tokens, max_terms)


def is_generic_title(title: str) -> bool:
    toks = set(tokenize(title))
    if not toks:
        return True
    return len(toks - GENERIC_TITLE_TERMS) <= 2


def _technical_density(text: str) -> float:
    toks = tokenize(text)
    if not toks:
        return 0.0
    generic = set([
        "question", "qualification", "performance", "contrainte", "comportement",
        "maitrise", "maîtrise", "solution", "systeme", "système", "permet-elle",
    ])
    useful = [t for t in toks if t not in generic]
    units = re.findall(r"\b\d+(?:[,.]\d+)?\s?(?:bar|bars|mpa|pa|kw|w|°c|c|m3/h|m³/h|hz|db|mm|cm|ms|s)\b", text, flags=re.I)
    return len(useful) + 2 * len(units)


def source_passages(verrou: Dict[str, Any]) -> List[str]:
    """
    Récupère les passages techniques source, pas les formulations Frascati.
    """
    passages = []

    raw = verrou.get("raw_item") or {}
    if isinstance(raw, dict):
        for key in ["text", "source_text"]:
            if raw.get(key):
                passages.append(str(raw.get(key)))

        supporting = raw.get("supporting_passages") or []
        if isinstance(supporting, list):
            for sp in supporting[:8]:
                if isinstance(sp, dict):
                    passages.append(str(sp.get("text") or sp.get("source_text") or ""))

    for s in (verrou.get("sources") or [])[:8]:
        if isinstance(s, dict):
            passages.append(str(s.get("excerpt") or ""))

    # fallback
    for k in ["text", "title", "verrou_title"]:
        if verrou.get(k):
            passages.append(str(verrou.get(k)))

    cleaned = []
    for p in passages:
        p = remove_frascati_question_text(p)
        p = clean_text(p, 1200)
        if len(p) >= 25:
            cleaned.append(p)

    cleaned.sort(key=_technical_density, reverse=True)
    return dedupe_keep_order(cleaned, 8)


def extract_source_text(verrou: Dict[str, Any]) -> str:
    passages = source_passages(verrou)
    return clean_text(" ".join(passages[:4]), 4500)


def extract_context_text(verrou: Dict[str, Any]) -> str:
    parts = []
    ctx = verrou.get("context") or {}
    if isinstance(ctx, dict):
        for key in ["objectifs", "methodes", "resultats", "parametres", "limites"]:
            val = ctx.get(key)
            if isinstance(val, list):
                parts.extend(str(x) for x in val[:3])
            elif isinstance(val, str):
                parts.append(val)

    diagnostic_context = verrou.get("diagnostic_context") or {}
    if diagnostic_context:
        parts.append(flatten_text(diagnostic_context, 2200))

    return remove_frascati_question_text(clean_text(" ".join(parts), 4000))


def filter_context_by_verrou(source_text: str, context_text: str, min_similarity: float = 0.04) -> str:
    if not context_text:
        return ""

    sentences = re.split(r"(?<=[.!?;])\s+|\n+", context_text)
    selected = []
    src_tokens = token_set(source_text)

    for sent in sentences:
        sent = remove_frascati_question_text(clean_text(sent, 500))
        if len(sent) < 30:
            continue

        stoks = token_set(sent)
        if not stoks:
            continue

        overlap = len(src_tokens & stoks) / max(8, min(len(src_tokens), 50))
        jac = jaccard(source_text, sent)
        score = max(overlap, jac)

        if score >= min_similarity:
            selected.append(sent)

        if len(selected) >= 3:
            break

    return clean_text(" ".join(selected), 1200)


def split_terms_by_role(terms: List[str]) -> Tuple[List[str], List[str], List[str]]:
    object_terms, phenomenon_terms, method_terms = [], [], []

    for term in terms:
        ntoks = set(norm(term).split())
        joined = norm(term)

        if ntoks & METHOD_MARKERS or any(x in joined for x in METHOD_MARKERS):
            method_terms.append(term)
        elif ntoks & PHENOMENON_MARKERS or any(x in joined for x in PHENOMENON_MARKERS):
            phenomenon_terms.append(term)
        else:
            object_terms.append(term)

    return (
        dedupe_keep_order(object_terms, 7),
        dedupe_keep_order(phenomenon_terms, 7),
        dedupe_keep_order(method_terms, 5),
    )


def extract_constraints(text: str) -> List[str]:
    constraints = []

    for m in re.finditer(r"\b\d+(?:[,.]\d+)?\s?(?:bar|bars|mpa|pa|kw|w|°c|c|m3/h|m³/h|hz|db|mm|cm|ms|s)\b", text, flags=re.I):
        constraints.append(m.group(0))

    sentences = re.split(r"(?<=[.!?;])\s+|\n+", text)
    for sent in sentences:
        ns = norm(sent)
        if any(marker in ns for marker in CONSTRAINT_MARKERS):
            sent = remove_frascati_question_text(clean_text(sent, 160))
            if sent:
                constraints.append(sent)

    return dedupe_keep_order(constraints, 5)


def choose_title(verrou: Dict[str, Any], source_text: str) -> str:
    for key in ["title", "verrou_title"]:
        t = clean_title(verrou.get(key))
        if t and not is_generic_title(t) and len(t) > 12:
            return clean_text(t, 140)

    # choisir le meilleur passage court
    passages = source_passages(verrou)
    for p in passages:
        first = re.split(r"(?<=[.!?])\s+", p)[0]
        first = clean_title(first)
        if len(first) > 25 and not is_generic_title(first):
            return clean_text(first, 140)

    return clean_text(source_text, 100) or "Verrou scientifique à analyser"


def build_scientific_intent(
    verrou: Dict[str, Any],
    domain_detection: Dict[str, Any] | None = None,
    diagnostic_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    domain_detection = domain_detection or {}
    diagnostic_context = diagnostic_context or {}

    v = dict(verrou)
    if diagnostic_context:
        v["diagnostic_context"] = diagnostic_context

    source_text = extract_source_text(v)
    context_text = extract_context_text(v)
    context_relevant = filter_context_by_verrou(source_text, context_text)
    domain_terms = extract_domain_terms(domain_detection)

    title = choose_title(v, source_text)

    source_terms_fr = extract_keyphrases(" ".join([title, source_text]), max_terms=22)
    context_terms_fr = extract_keyphrases(context_relevant, max_terms=8)

    source_token_ref = token_set(" ".join(source_terms_fr + domain_terms))
    context_kept = []
    for t in context_terms_fr:
        tt = token_set(t)
        if tt and (tt & source_token_ref):
            context_kept.append(t)
        if len(context_kept) >= 4:
            break

    key_terms_fr = dedupe_keep_order(source_terms_fr + context_kept + domain_terms, 16)
    key_terms_en = translate_terms(key_terms_fr)

    obj_terms, phenomenon_terms, method_terms = split_terms_by_role(key_terms_en)

    # objet technique : éviter de mettre les termes génériques de qualification
    technical_object_terms = []
    for t in obj_terms + domain_terms:
        nt = norm(t)
        if any(g in nt for g in ["question", "qualification", "permet", "maitrise", "maîtrise"]):
            continue
        technical_object_terms.append(t)

    technical_object = clean_text(" ".join(dedupe_keep_order(technical_object_terms, 6)), 180)
    phenomenon = clean_text(" ".join(phenomenon_terms[:6] or key_terms_en[:5]), 180)
    methods = dedupe_keep_order(method_terms, 5)
    constraints = extract_constraints(" ".join([source_text, context_relevant]))

    scientific_problem = clean_text(
        " ".join([technical_object, phenomenon, " ".join(constraints[:2])]),
        280,
    )
    if not scientific_problem:
        scientific_problem = clean_text(" ".join(key_terms_en[:8]), 240)

    confidence = 0.45
    if len(key_terms_en) >= 6:
        confidence += 0.20
    if technical_object:
        confidence += 0.15
    if phenomenon:
        confidence += 0.15
    if context_relevant:
        confidence += 0.05
    confidence = min(confidence, 0.95)

    enrichment_profile = detect_enrichment_profile_from_text(
        title,
        source_text,
        context_relevant,
        " ".join(key_terms_fr),
        " ".join(key_terms_en),
        domain_detection.get("display_label") or domain_detection.get("main_domain_label") or "",
    )

    intent = ScientificIntent(
        verrou_id=str(v.get("verrou_id") or ""),
        verrou_title=title,
        scientific_problem=scientific_problem,
        technical_object=technical_object,
        phenomenon=phenomenon,
        constraints=constraints,
        methods=methods,
        key_terms_fr=key_terms_fr,
        key_terms_en=key_terms_en,
        search_queries=[],
        source_basis={
            "title": title,
            "source_text_excerpt": clean_text(source_text, 900),
            "context_relevant_excerpt": clean_text(context_relevant, 700),
            "domain_terms": domain_terms,
            "context_filter": "source_evidence_first_similarity",
        },
        confidence=round(confidence, 4),
    )

    out = intent.to_dict()
    out["enrichment_profile"] = enrichment_profile
    out["backend_enrichment_profile"] = enrichment_profile

    # V130 : profil CIR complet issu de la nomenclature tous domaines.
    # Ce profil ne remplace pas l'ancien enrichment_profile ; il l'enrichit
    # pour construire des requêtes adaptées à tous les domaines CIR.
    cir_profile = get_cir_domain_profile(
        domain_detection=domain_detection,
        text=" ".join([
            title,
            source_text,
            context_relevant,
            " ".join(key_terms_fr),
            " ".join(key_terms_en),
        ]),
    )
    out["cir_domain_profile"] = cir_profile
    out["domain_detection"] = domain_detection
    out["cir_domain_detection"] = domain_detection

    # Ajout léger de termes de domaine pour aider les requêtes, sans écraser
    # les termes extraits des preuves sources.
    extra_terms = list(cir_profile.get("positive_terms") or []) + list(cir_profile.get("domain_terms") or [])
    out["key_terms_en"] = dedupe_keep_order((out.get("key_terms_en") or []) + extra_terms, 24)

    out["query_builder_version"] = "v130_cir_domain_catalog"
    return out
