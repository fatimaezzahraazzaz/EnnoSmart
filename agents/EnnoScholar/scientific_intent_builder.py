# -*- coding: utf-8 -*-
from __future__ import annotations

"""
scientific_intent_builder.py — EnnoScholar V146 scientific roles and acronym disambiguation

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

INTENT_NOISE_TERMS = {
    "avec", "sans", "dans", "pour", "sur", "sous", "entre", "vers", "chez",
    "sont", "est", "etre", "être", "qui", "que", "dont", "mais", "plus", "moins",
    "grand", "grands", "grande", "grandes", "nouveau", "nouvelle", "utilise", "utiliser",
    "projet", "dossier", "consultant", "cir", "frascati", "nlp", "rag", "llm",
    "ennodiagnostic", "ennoscholar", "signal", "preuve", "preuves", "passage", "passages",
    "question", "qualification", "incertitude", "verrou", "scientifique", "technique",
    "method", "methods", "methode", "méthode", "model", "models", "modele", "modèle",
    "system", "systems", "systeme", "système", "study", "paper", "article", "results",
    "resultat", "résultat", "performance", "evaluation", "validation", "comparison",
    "comparaison", "approach", "approche", "using", "based", "software", "logiciel",
}

ADMIN_ACRONYMS = {
    "CIR", "RND", "RD", "R&D", "NLP", "RAG", "LLM", "IA", "AI", "API", "JSON",
    "PDF", "DOCX", "HTTP", "HTTPS", "DB", "SQL", "UI", "UX",
}


def _clean_local_terms(values: List[str], max_terms: int = 18) -> List[str]:
    """Conserve seulement les expressions réellement informatives du verrou courant."""
    out: List[str] = []
    seen = set()
    for raw in values or []:
        value = clean_text(raw, 100)
        nv = norm(value)
        if not nv:
            continue
        toks = [t for t in tokenize(nv) if t and t not in INTENT_NOISE_TERMS and len(t) >= 3]
        if not toks:
            continue
        # Une expression longue doit contenir au moins deux termes informatifs.
        if len(nv.split()) >= 2 and len(toks) < 2:
            continue
        # Évite les fragments de phrases issus des n-grammes automatiques.
        if len(value.split()) > 5:
            continue
        key = " ".join(toks)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
        if len(out) >= max_terms:
            break
    return out


def _extract_local_names_and_acronyms(text: str, max_items: int = 12) -> List[str]:
    """Noms propres/sigles uniquement depuis le titre et les preuves du verrou courant."""
    raw = str(text or "")
    out: List[str] = []
    seen = set()
    for token in re.findall(r"\b[A-Z][A-Z0-9]{1,}(?:[-_/][A-Z0-9]+)?\b", raw):
        if token.upper() in ADMIN_ACRONYMS:
            continue
        key = norm(token)
        if key and key not in seen:
            seen.add(key)
            out.append(token)
    for token in re.findall(r"\b[A-Z][a-zA-Z0-9_-]{3,}\b", raw):
        if token.upper() in ADMIN_ACRONYMS:
            continue
        key = norm(token)
        if key and key not in seen and key not in INTENT_NOISE_TERMS:
            seen.add(key)
            out.append(token)
    return out[:max_items]


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
            # Le titre de section contient souvent le début indispensable de
            # la phrase (méthode, configuration, paramètre), tandis que
            # ``excerpt`` commence juste après. Les séparer faisait disparaître
            # des ancres comme « ray-launch density » ou « bistatic radar ».
            section_title = clean_text(s.get("section_title"), 500)
            excerpt = clean_text(s.get("excerpt") or s.get("text"), 3500)
            if section_title and excerpt:
                passages.append(f"{section_title}. {excerpt}")
            elif section_title or excerpt:
                passages.append(section_title or excerpt)

    # fallback
    for k in ["text", "title", "research_target_title", "verrou_title"]:
        if verrou.get(k):
            passages.append(str(verrou.get(k)))

    cleaned = []
    for p in passages:
        p = remove_frascati_question_text(p)
        p = clean_text(p, 4000)
        if len(p) >= 25:
            cleaned.append(p)

    cleaned.sort(key=_technical_density, reverse=True)
    # ``dedupe_keep_order`` est adapte aux mots-cles et tronque volontairement
    # chaque element a 120 caracteres. Il ne doit jamais etre utilise pour des
    # passages sources : cette troncature supprimait presque tout le contenu de
    # la section avant l'extraction de l'intention scientifique.
    output: List[str] = []
    seen = set()
    for passage in cleaned:
        value = clean_text(passage, 4000)
        key = norm(value)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(value)
        if len(output) >= 8:
            break
    return output


def extract_source_text(verrou: Dict[str, Any]) -> str:
    passages = source_passages(verrou)
    return clean_text(" ".join(passages[:4]), 8000)


def extract_context_text(verrou: Dict[str, Any]) -> str:
    parts = []
    ctx = verrou.get("context") or {}
    if isinstance(ctx, dict):
        for key in [
            "objectifs", "methodes", "resultats", "parametres", "limites",
            "local_context", "context_text", "section_context",
        ]:
            val = ctx.get(key)
            if isinstance(val, list):
                parts.extend(str(x) for x in val[:3])
            elif isinstance(val, str):
                parts.append(val)

    research_context = verrou.get("research_context") or {}
    if isinstance(research_context, dict):
        for key in ["local_context", "context_text", "section_context"]:
            value = research_context.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value)

    diagnostic_context = verrou.get("diagnostic_context") or {}
    if diagnostic_context:
        parts.append(flatten_text(diagnostic_context, 2200))

    return remove_frascati_question_text(clean_text(" ".join(parts), 4000))



def filter_context_by_verrou(source_text: str, context_text: str, min_similarity: float = 0.10) -> str:
    """
    Ne conserve du contexte global que les phrases réellement reliées aux preuves
    du verrou courant. Une simple proximité thématique ne suffit plus.
    """
    if not context_text or not source_text:
        return ""

    sentences = re.split(r"(?<=[.!?;])\s+|\n+", context_text)
    selected: List[Tuple[float, str]] = []
    src_tokens = {
        t for t in token_set(source_text)
        if t not in INTENT_NOISE_TERMS and len(t) >= 3
    }
    if not src_tokens:
        return ""

    for sent in sentences:
        sent = remove_frascati_question_text(clean_text(sent, 500))
        if len(sent) < 35:
            continue

        stoks = {
            t for t in token_set(sent)
            if t not in INTENT_NOISE_TERMS and len(t) >= 3
        }
        common = src_tokens & stoks
        if len(common) < 2:
            continue

        overlap = len(common) / max(6, min(len(src_tokens), 35))
        jac = jaccard(" ".join(sorted(src_tokens)), " ".join(sorted(stoks)))
        score = max(overlap, jac)
        if score >= min_similarity:
            selected.append((score, sent))

    selected.sort(key=lambda x: x[0], reverse=True)
    return clean_text(" ".join(sent for _, sent in selected[:2]), 900)

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
    for key in ["title", "research_target_title", "verrou_title"]:
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
    """Construit une intention scientifique strictement locale au verrou courant."""
    domain_detection = domain_detection or {}
    diagnostic_context = diagnostic_context or {}

    v = dict(verrou)
    if diagnostic_context:
        v["diagnostic_context"] = diagnostic_context

    source_text = extract_source_text(v)
    context_text = extract_context_text(v)
    context_relevant = filter_context_by_verrou(source_text, context_text, min_similarity=0.10)
    title = choose_title(v, source_text)

    local_text = clean_text(" ".join([title, source_text]), 5000)
    local_names = _extract_local_names_and_acronyms(local_text, max_items=12)

    source_terms_fr = _clean_local_terms(
        extract_keyphrases(local_text, max_terms=28),
        max_terms=18,
    )
    context_terms_fr = _clean_local_terms(
        extract_keyphrases(context_relevant, max_terms=8),
        max_terms=4,
    )

    source_token_ref = {
        t for t in token_set(" ".join(source_terms_fr + local_names))
        if t not in INTENT_NOISE_TERMS
    }
    context_kept: List[str] = []
    for term in context_terms_fr:
        tt = {t for t in token_set(term) if t not in INTENT_NOISE_TERMS}
        if len(tt & source_token_ref) >= 2:
            context_kept.append(term)

    key_terms_fr = dedupe_keep_order(local_names + source_terms_fr + context_kept, 18)
    key_terms_en = _clean_local_terms(translate_terms(key_terms_fr), max_terms=18)

    obj_terms, phenomenon_terms, method_terms = split_terms_by_role(key_terms_en)

    technical_object_terms: List[str] = []
    for term in obj_terms:
        ntoks = [t for t in tokenize(norm(term)) if t not in INTENT_NOISE_TERMS]
        if ntoks:
            technical_object_terms.append(term)

    technical_object = clean_text(
        " ".join(dedupe_keep_order(technical_object_terms, 6)),
        180,
    )
    phenomenon = clean_text(
        " ".join(_clean_local_terms(phenomenon_terms, 5)),
        180,
    )
    methods = _clean_local_terms(method_terms, 6)
    constraints = extract_constraints(" ".join([source_text, context_relevant]))

    scientific_problem = clean_text(
        " ".join(x for x in [technical_object, phenomenon, " ".join(constraints[:2])] if x),
        320,
    )
    if not scientific_problem:
        scientific_problem = clean_text(" ".join(key_terms_en[:8]), 260)

    # Profil local du verrou. Le domaine global du projet ne sert qu'en fallback.
    local_profile = get_cir_domain_profile(
        domain_detection={},
        text=" ".join([title, source_text, technical_object, phenomenon]),
    )
    local_profile_id = str(local_profile.get("profile_id") or "generic")
    if local_profile_id == "generic":
        cir_profile = get_cir_domain_profile(
            domain_detection=domain_detection,
            text=" ".join([title, source_text, technical_object, phenomenon]),
        )
        profile_source = "project_domain_fallback"
    else:
        cir_profile = local_profile
        profile_source = "local_verrou_evidence"

    # Ancres fortes : sigles/noms + expressions multi-mots venant des preuves.
    phrase_anchors = [
        term for term in key_terms_en + key_terms_fr
        if len(norm(term).split()) >= 2 and len(norm(term)) >= 7
    ]
    strong_anchors = dedupe_keep_order(local_names + phrase_anchors, 16)

    confidence = 0.40
    if len(key_terms_en) >= 6:
        confidence += 0.20
    if technical_object:
        confidence += 0.15
    if phenomenon or methods:
        confidence += 0.10
    if strong_anchors:
        confidence += 0.10
    if context_relevant:
        confidence += 0.05
    confidence = min(confidence, 0.95)

    enrichment_profile = detect_enrichment_profile_from_text(
        title,
        source_text,
        context_relevant,
        " ".join(key_terms_fr),
        " ".join(key_terms_en),
    )

    intent = ScientificIntent(
        verrou_id=str(
            v.get("research_target_id")
            or v.get("target_id")
            or v.get("verrou_id")
            or ""
        ),
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
            "source_text_excerpt": clean_text(source_text, 1400),
            "context_relevant_excerpt": clean_text(context_relevant, 700),
            "domain_terms": [],
            "context_filter": "v145_local_evidence_two_token_overlap",
        },
        confidence=round(confidence, 4),
    )

    out = intent.to_dict()
    out["strong_anchors"] = strong_anchors
    out["local_names"] = local_names
    out["enrichment_profile"] = enrichment_profile
    out["backend_enrichment_profile"] = enrichment_profile
    out["cir_domain_profile"] = cir_profile
    out["cir_profile_source"] = profile_source
    out["domain_detection"] = domain_detection
    out["cir_domain_detection"] = domain_detection
    research_target_id = str(
        v.get("research_target_id")
        or v.get("target_id")
        or ""
    )
    research_target_type = str(v.get("research_target_type") or "").strip()
    if research_target_id:
        out["research_target_id"] = research_target_id
        out["research_target_title"] = title
        out["research_target_type"] = research_target_type or "scientific_enrichment"
        out["intent_scope"] = "current_research_target_evidence_only"
        out["subject_kind"] = "research_target"
    else:
        out["intent_scope"] = "current_verrou_evidence_only"
        out["subject_kind"] = "diagnostic_lock"
    out["query_builder_version"] = "v145_local_evidence_intent"
    return out



# =============================================================================
# V146 — rôles scientifiques, désambiguïsation et concepts canoniques
# =============================================================================
_BUILD_SCIENTIFIC_INTENT_V145 = build_scientific_intent

_V146_IMPLEMENTATION_TERMS = {
    "cpu", "gpu", "cuda", "opencl", "parallel", "parallelisation", "parallelization",
    "implementation", "implémentation", "runtime", "vectorization", "multithreading",
}

_V146_METHOD_ONTOLOGY = [
    (["method of moments", "méthode des moments", "methode des moments", " mom "], "method of moments"),
    (["multilevel fast multipole method", "multi level fast multipole", "mlfmm", "mflmm"], "multilevel fast multipole method"),
    (["uniform theory of diffraction", "théorie uniforme de la diffraction", "theorie uniforme de la diffraction", " utd ", " tud "], "uniform theory of diffraction"),
    (["physical optics", "optique physique", " op ", " po "], "physical optics"),
    ([
        "ray tracing", "ray launching", "ray launch", "ray-launch",
        "lancer de rayon", "lancer de rayons", "tracé de rayons", "trace de rayons",
    ], "electromagnetic ray tracing"),
    (["finite-difference time-domain", "finite difference time domain", "différences finies dans le domaine temporel", "differences finies dans le domaine temporel", " fdtd "], "finite-difference time-domain"),
    (["finite element method", "finite-element method", "éléments finis", "elements finis", " fem "], "finite element method"),
    (["full-wave", "full wave", "méthodes exactes", "methodes exactes"], "full-wave electromagnetic method"),
    (["scattering center", "scattering centre", "centre brillant", "point brillant"], "scattering-centre model"),
]

_V146_CONCEPT_ALIASES = {
    "radar cross section": ["radar cross section", "rcs", "surface équivalente radar", "surface equivalente radar"],
    "synthetic aperture radar": ["synthetic aperture radar"],
    "automatic target recognition": ["automatic target recognition", "target recognition"],
    "electromagnetic scattering": ["electromagnetic scattering", "diffusion électromagnétique", "diffusion electromagnetique"],
    "edge diffraction": ["edge diffraction", "diffraction des arêtes", "diffraction des aretes"],
    "electromagnetic ray tracing": ["electromagnetic ray tracing", "ray tracing", "ray launching", "lancer de rayons", "lancer de rayon"],
    "ray-launch density": [
        "ray-launch density", "ray launch density", "ray density",
        "densité des rayons", "densite des rayons",
    ],
    "bistatic radar simulation": [
        "bistatic radar simulation", "bistatic radar", "bistatic SAR",
        "radar bistatique", "radar bistatiques",
    ],
    "large complex electromagnetic structures": ["large complex electromagnetic structures", "large electromagnetic structures", "structures de grande taille", "très grands systèmes", "tres grands systemes"],
    "canonical electromagnetic targets": ["canonical electromagnetic targets", "canonical target", "objet canonique", "cible canonique", "sphere", "plaque", "dièdre", "diedre", "trièdre", "triedre"],
    "mesh discretization": ["mesh discretization", "mesh approximation", "maillage", "objet 3d maillé", "objet 3d maille"],
    "synthetic training data": [
        "synthetic training data", "synthetic data", "synthetic dataset",
        "synthetically generated data", "simulated training data", "simulation data",
        "synthetic sar data", "synthetic sar images", "generated sar images",
        "données synthétiques", "donnees synthetiques",
        "jeu de données synthétique", "jeu de donnees synthetique",
    ],
    "sim-to-real generalization": [
        "sim-to-real", "simulation-to-real", "synthetic-to-real",
        "generalization to real", "generalisation to real", "domain gap",
        "domain adaptation", "domain generalization", "domain generalisation",
        "cross-domain transfer", "synthetic-to-measured",
        "écart synthétique réel", "ecart synthetique reel",
    ],
    "domain shift": ["domain shift", "dataset shift", "distribution shift", "décalage de domaine", "decalage de domaine", "écart de distribution", "ecart de distribution"],
    "real radar measurements": [
        "real radar measurements", "real measurements", "measured sar data",
        "measured sar images", "real sar data", "real-world sar data",
        "synthetic and measured", "measured data", "mstar measurements",
        "mstar dataset", "sample dataset", "mesures réelles", "mesures reelles",
        "données réelles", "donnees reelles",
    ],
}


def _v146_has_exact(text_norm: str, phrase: str) -> bool:
    p = norm(phrase)
    if not p:
        return False
    pattern = re.escape(p).replace(r"\ ", r"\s+")
    return bool(re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", text_norm))


def _v146_add_unique(items: List[str], value: str, limit: int = 20) -> None:
    value = clean_text(value, 120)
    if value and norm(value) not in {norm(x) for x in items} and len(items) < limit:
        items.append(value)


def _v146_scientific_roles(local_text: str, previous: Dict[str, Any]) -> Dict[str, Any]:
    n = " " + norm(local_text) + " "
    methods: List[str] = []
    concepts: List[str] = []
    phenomena: List[str] = []
    implementation: List[str] = []
    project_tools: List[str] = []
    acronym_expansions: Dict[str, str] = {}
    ambiguous: List[str] = []

    # Méthodes canoniques.
    for aliases, canonical in _V146_METHOD_ONTOLOGY:
        if any(_v146_has_exact(n, alias) for alias in aliases):
            _v146_add_unique(methods, canonical)

    # Termes d'implémentation : conservés comme métadonnées, jamais comme coeur scientifique.
    for term in sorted(_V146_IMPLEMENTATION_TERMS):
        if _v146_has_exact(n, term):
            _v146_add_unique(implementation, term)

    em_context = sum(1 for t in [
        "feko", "method of moments", "mom", "mlfmm", "mflmm", "fdtd", "fem",
        "utd", "tud", "physical optics", "optique physique", "ray tracing",
        "lancer de rayon", "diffraction", "electromagnetic", "électromagnétique",
    ] if _v146_has_exact(n, t))
    radar_context = sum(1 for t in [
        "radar", "mstar", "target recognition", "reconnaissance de cibles", "atr",
        "mocem", "salsa", "scattering", "diffusion électromagnétique",
    ] if _v146_has_exact(n, t))

    # SER est ambigu : expansion seulement si le contexte électromagnétique la confirme.
    if _v146_has_exact(n, "ser"):
        ambiguous.append("SER")
        if em_context >= 2 or radar_context >= 2:
            acronym_expansions["SER"] = "radar cross section"
            _v146_add_unique(concepts, "radar cross section")

    # SAR + ATR dans le même verrou désambiguïsent mutuellement radar et reconnaissance de cibles.
    has_sar_token = _v146_has_exact(n, "sar")
    has_atr_token = _v146_has_exact(n, "atr")

    # SAR/ATR sont eux aussi ambigus : expansion conditionnée au contexte radar/cible.
    if has_sar_token:
        ambiguous.append("SAR")
        if (
            has_atr_token
            or radar_context >= 2
            or _v146_has_exact(n, "synthetic aperture radar")
            or _v146_has_exact(n, "radar")
        ):
            acronym_expansions["SAR"] = "synthetic aperture radar"
            _v146_add_unique(concepts, "synthetic aperture radar")
    if has_atr_token:
        ambiguous.append("ATR")
        if has_sar_token or radar_context >= 2 or _v146_has_exact(n, "automatic target recognition"):
            acronym_expansions["ATR"] = "automatic target recognition"
            _v146_add_unique(concepts, "automatic target recognition")
    if _v146_has_exact(n, "rcs"):
        acronym_expansions["RCS"] = "radar cross section"
        _v146_add_unique(concepts, "radar cross section")

    # Concepts physiques et expérimentaux locaux.
    if em_context >= 2:
        _v146_add_unique(concepts, "electromagnetic scattering")
    # FEKO + diffraction/ray tracing/cibles canoniques désigne généralement un problème de RCS.
    if (_v146_has_exact(n, "feko") and em_context >= 2
        and any(_v146_has_exact(n, t) for t in ["diffraction", "ray tracing", "lancer de rayon", "objet canonique", "sphere", "plaque"])):
        if "radar cross section" not in concepts:
            concepts.insert(0, "radar cross section")
    if any(_v146_has_exact(n, t) for t in ["diffraction des arêtes", "diffraction des aretes", "edge diffraction"]):
        _v146_add_unique(concepts, "edge diffraction")
        _v146_add_unique(phenomena, "omitted edge-diffraction phenomena")
    if any(_v146_has_exact(n, t) for t in [
        "lancer de rayon", "lancer de rayons", "ray tracing", "ray launching",
        "ray launch", "ray-launch",
    ]):
        _v146_add_unique(concepts, "electromagnetic ray tracing")
    if any(_v146_has_exact(n, t) for t in [
        "ray-launch density", "ray launch density", "ray density",
        "densité des rayons", "densite des rayons",
    ]):
        _v146_add_unique(concepts, "ray-launch density")
    if any(_v146_has_exact(n, t) for t in [
        "bistatic radar", "bistatic sar", "radar bistatique", "radar bistatiques",
    ]):
        _v146_add_unique(concepts, "bistatic radar simulation")
    if any(_v146_has_exact(n, t) for t in ["très grands systèmes", "tres grands systemes", "structures de grande taille", "large structures"]):
        if em_context >= 1:
            _v146_add_unique(concepts, "large complex electromagnetic structures")
    if any(_v146_has_exact(n, t) for t in ["objet canonique", "cible canonique", "sphere", "plaque", "dièdre", "diedre", "trièdre", "triedre"]):
        if em_context >= 1:
            _v146_add_unique(concepts, "canonical electromagnetic targets")
    if any(_v146_has_exact(n, t) for t in ["maillage", "mesh discretization", "mesh approximation", "objet 3d maillé", "objet 3d maille"]):
        _v146_add_unique(concepts, "mesh discretization")

    if any(_v146_has_exact(n, t) for t in ["données synthétiques", "donnees synthetiques", "synthetic data", "synthetic dataset"]):
        _v146_add_unique(concepts, "synthetic training data")
    if any(_v146_has_exact(n, t) for t in ["conditions réelles", "conditions reelles", "mesures réelles", "mesures reelles", "real measurements", "mstar"]):
        _v146_add_unique(concepts, "real radar measurements")
    if any(_v146_has_exact(n, t) for t in ["généralisation", "generalisation", "generalization", "sim-to-real", "simulation-to-real", "ne peuvent pas généraliser", "ne peuvent pas generaliser"]):
        _v146_add_unique(concepts, "sim-to-real generalization")
        _v146_add_unique(phenomena, "limited sim-to-real generalization")
    if any(_v146_has_exact(n, t) for t in ["dataset shift", "domain shift", "distribution shift", "décalage de domaine", "decalage de domaine", "écart de distribution", "ecart de distribution"]):
        _v146_add_unique(concepts, "domain shift")
        _v146_add_unique(phenomena, "synthetic-to-real distribution shift")

    # Phénomènes/contraintes centrales.
    if any(_v146_has_exact(n, t) for t in [
        "ressources computationnelles", "computational resources", "temps de calcul",
        "mémoire", "memoire", "computational cost", "computing speed",
        "computational speed", "performance calculatoire",
    ]):
        _v146_add_unique(phenomena, "computational cost and memory requirements")
    if any(_v146_has_exact(n, t) for t in [
        "compromis précision", "compromis precision", "accuracy trade-off",
        "trade-off between accuracy and computing",
        "trade off between accuracy and computing",
        "accuracy and computing speed", "précision mais", "precision mais",
    ]):
        _v146_add_unique(phenomena, "accuracy-computational cost trade-off")
    if any(_v146_has_exact(n, t) for t in ["non modélisés", "non modelises", "non-modélisation", "non-modelisation", "simplifying assumptions"]):
        _v146_add_unique(phenomena, "model-form error from omitted physical phenomena")
    if any(_v146_has_exact(n, t) for t in ["niveau de représentativité", "niveau de representativite", "représentativité", "representativite", "representativeness"]):
        _v146_add_unique(phenomena, "uncertain physical representativeness")
    if any(_v146_has_exact(n, t) for t in ["valider", "validation", "benchmark", "comparaison aux résultats", "comparaison aux resultats"]):
        if concepts:
            _v146_add_unique(phenomena, "validation against reference methods or measurements")

    # Noms propres : utiles seulement en complément d'un concept scientifique.
    standardized = {norm(x) for x in methods + concepts}
    for name in previous.get("local_names") or []:
        nn = norm(name)
        if (not nn or nn.startswith("verro") or name.upper() in ADMIN_ACRONYMS
            or nn in {"incertitude", "les", "le", "la", "afin", "dans", "pour", "domaine", "projet",
                      "this", "that", "these", "those", "dataset", "data", "acquiring", "however", "results",
                      "method", "methods", "study", "article", "figure", "table", "using", "based", "according"}):
            continue
        # Un mot simplement capitalisé en début de phrase n'est pas un nom d'outil.
        occurrences = len(re.findall(rf"(?<![a-z0-9]){re.escape(nn)}(?![a-z0-9])", n))
        mixed_case = any(c.islower() for c in str(name)) and any(c.isupper() for c in str(name)[1:])
        if not str(name).isupper() and not mixed_case and occurrences < 2:
            continue
        if nn in _V146_IMPLEMENTATION_TERMS:
            continue
        if nn not in standardized and name.upper() not in {"SER", "SAR", "ATR", "RCS", "FEM", "FDTD", "MOM", "MLFMM", "MFLMM", "UTD", "TUD", "OP", "PO", "RL"}:
            _v146_add_unique(project_tools, name, 8)

    # Fallback générique pour les autres domaines : deux expressions multi-mots locales.
    if not concepts:
        for term in previous.get("strong_anchors") or []:
            nt = norm(term)
            if len(nt.split()) >= 2 and not any(x in nt for x in ["technical uncertainty", "uncertainty", "validation", "performance"]):
                _v146_add_unique(concepts, term, 4)

    # Concepts primaires : objet physique/métier indispensable. Les concepts comme
    # synthetic data, real measurements ou computational cost restent secondaires.
    if "synthetic aperture radar" in concepts or "automatic target recognition" in concepts:
        primary = [c for c in [
            "synthetic aperture radar", "automatic target recognition",
            "electromagnetic ray tracing", "ray-launch density",
            "bistatic radar simulation",
        ] if c in concepts]
    elif "radar cross section" in concepts:
        primary = [c for c in ["radar cross section", "electromagnetic scattering"] if c in concepts]
    elif any(c in concepts for c in ["edge diffraction", "electromagnetic ray tracing"]):
        primary = [c for c in ["electromagnetic scattering", "edge diffraction", "electromagnetic ray tracing"] if c in concepts]
    else:
        primary = concepts[:2]

    aliases = {c: list(_V146_CONCEPT_ALIASES.get(c, [c])) for c in concepts}
    if "synthetic aperture radar" in concepts:
        aliases["synthetic aperture radar"] = ["synthetic aperture radar", "SAR"]
    if "automatic target recognition" in concepts:
        aliases["automatic target recognition"] = ["automatic target recognition", "ATR"]
    if "radar cross section" in concepts:
        aliases["radar cross section"] = ["radar cross section", "RCS", "surface équivalente radar", "surface equivalente radar", "SER"]

    return {
        "core_concepts": concepts[:12],
        "primary_core_concepts": primary[:4],
        "method_anchors": methods[:10],
        "phenomenon_anchors": phenomena[:10],
        "implementation_terms": implementation[:10],
        "project_tool_terms": project_tools[:8],
        "acronym_expansions": acronym_expansions,
        "ambiguous_acronyms": list(dict.fromkeys(ambiguous)),
        "concept_aliases": aliases,
    }


def _dynamic_source_anchors(title: str, source_text: str) -> Dict[str, Any]:
    """Extrait des ancres discriminantes sans vocabulaire metier predefini.

    Les ancres viennent exclusivement du titre et du passage courant. Les
    acronymes ne sont retenus que s'ils sont repetes dans la source; les groupes
    de mots sont classes par recurrence et par specificite locale.
    """

    title_text = clean_text(title, 800)
    source = clean_text(source_text, 8000)
    source_norm = norm(source)
    source_tokens = tokenize(source)
    title_tokens = [
        token
        for token in tokenize(title_text)
        if token not in INTENT_NOISE_TERMS and not token.replace(".", "").isdigit()
    ]

    acronyms: List[str] = []
    for value in re.findall(r"\b[A-Z][A-Z0-9]{1,}(?:[-_/][A-Z0-9]+)?\b", source):
        if value.upper() in ADMIN_ACRONYMS:
            continue
        occurrences = len(
            re.findall(rf"(?<![A-Z0-9]){re.escape(value)}(?![A-Z0-9])", source)
        )
        if occurrences >= 2 and value not in acronyms:
            acronyms.append(value)

    acronym_norm = {norm(value) for value in acronyms}
    frequency: Dict[str, int] = {}
    for token in source_tokens:
        frequency[token] = frequency.get(token, 0) + 1

    phrase_candidates: List[Tuple[float, str]] = []
    for size in (3, 2):
        for index in range(0, max(0, len(title_tokens) - size + 1)):
            words = title_tokens[index:index + size]
            if len(set(words)) < 2:
                continue
            phrase = " ".join(words)
            score = float(sum(min(frequency.get(word, 0), 4) for word in words))
            if any(word in acronym_norm for word in words):
                score += 4.0
            score += min(source_norm.count(phrase), 3) * 1.5
            phrase_candidates.append((score, phrase))

    phrase_candidates.sort(key=lambda row: (-row[0], -len(row[1]), row[1]))
    phrases: List[str] = []
    for _, phrase in phrase_candidates:
        tokens = set(phrase.split())
        if any(
            len(tokens & set(existing.split())) / max(1, len(tokens | set(existing.split())))
            >= 0.80
            for existing in phrases
        ):
            continue
        phrases.append(phrase)
        if len(phrases) >= 6:
            break

    terms = sorted(
        set(title_tokens),
        key=lambda token: (-frequency.get(token, 0), -len(token), token),
    )[:12]
    return {
        "literal_source_acronyms": acronyms[:6],
        "literal_source_phrases": phrases,
        "literal_source_terms": terms,
        "literal_source_anchor_policy": "derived_from_current_title_and_source_only",
    }


def build_scientific_intent(
    verrou: Dict[str, Any],
    domain_detection: Dict[str, Any] | None = None,
    diagnostic_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    out = _BUILD_SCIENTIFIC_INTENT_V145(verrou, domain_detection, diagnostic_context)
    sb = out.get("source_basis") if isinstance(out.get("source_basis"), dict) else {}
    full_source_text = extract_source_text(dict(verrou or {}))
    local_text = " ".join([
        str(out.get("verrou_title") or ""),
        full_source_text,
        str(sb.get("source_text_excerpt") or ""),
        " ".join(map(str, out.get("key_terms_fr") or [])),
    ])
    roles = _v146_scientific_roles(local_text, out)
    out.update(roles)
    dynamic_anchors = _dynamic_source_anchors(
        str(out.get("verrou_title") or ""),
        full_source_text,
    )
    out.update(dynamic_anchors)

    core = roles["core_concepts"]
    methods = roles["method_anchors"]
    phenomena = roles["phenomenon_anchors"]
    tools = roles["project_tool_terms"]

    if core:
        out["technical_object"] = clean_text(" ".join(core[:3]), 240)
    if phenomena:
        out["phenomenon"] = clean_text(" ".join(phenomena[:3]), 240)
    if methods:
        out["methods"] = methods[:8]

    out["scientific_problem"] = clean_text(
        " ".join(core[:3] + phenomena[:2] + methods[:2]),
        420,
    ) or out.get("scientific_problem")

    # Les ancres fortes ne contiennent plus uncertainty/CPU/GPU/noms tronqués.
    out["strong_anchors"] = dedupe_keep_order(
        list(dynamic_anchors.get("literal_source_acronyms") or [])
        + list(dynamic_anchors.get("literal_source_phrases") or [])
        + core
        + methods
        + phenomena
        + tools,
        24,
    )
    out["key_terms_en"] = dedupe_keep_order(core + methods + phenomena + list(out.get("key_terms_en") or []), 28)
    out["intent_scope"] = "v155_current_verrou_section_aware_roles"
    out["query_builder_version"] = "v155_section_aware_problem_evidence_intent"
    if isinstance(out.get("source_basis"), dict):
        out["source_basis"]["context_filter"] = "v146_local_evidence_roles_and_acronym_disambiguation"
    return out
