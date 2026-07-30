# -*- coding: utf-8 -*-
from __future__ import annotations

"""Generic scientific-query normalization for EnnoScholar.

The module is deliberately project-agnostic. It separates local product/tool names
from transferable scientific concepts and builds an English research vocabulary
from evidence text. No customer, project, software or domain is hard-coded.
"""

import re
import unicodedata
from collections import Counter
from typing import Any, Dict, Iterable, List

_GENERIC = {
    "incertitude", "uncertainty", "verrou", "technical", "scientific", "technique",
    "validation", "validate", "validity", "performance", "performances", "result", "results", "prennent", "prendre", "compte", "exactes", "exacte",
    "validite", "validity", "methodes", "methode", "logiciel", "study", "project", "software", "tool", "system", "method", "methods",
    "model", "models", "approach", "analysis", "comparison", "using", "based",
    "cpu", "gpu", "cuda", "implementation", "implementations", "document", "source",
    "evidence", "consultant", "research", "development", "problem", "issue",
}

_STOP = _GENERIC | {
    "avec", "sans", "dans", "pour", "par", "sur", "sous", "entre", "vers", "afin",
    "les", "des", "une", "un", "du", "de", "la", "le", "et", "ou", "est", "sont",
    "qui", "que", "dont", "ce", "cette", "ces", "au", "aux", "en", "plus", "moins",
    "the", "and", "or", "of", "to", "in", "on", "for", "with", "without", "from",
    "this", "that", "these", "those", "is", "are", "be", "been", "as", "at", "by",
}

# Generic scientific translations. These are field concepts, not project profiles.
_TRANSLATE = {
    "électromagnétique": "electromagnetic", "electromagnetique": "electromagnetic",
    "électromagnétisme": "electromagnetics", "electromagnetisme": "electromagnetics",
    "diffusion": "scattering", "diffraction": "diffraction", "arête": "edge", "arete": "edge",
    "rayon": "ray", "rayons": "rays", "lancer": "tracing", "optique": "optics",
    "géométrique": "geometrical", "geometrique": "geometrical", "physique": "physical",
    "asymptotique": "asymptotic", "haute fréquence": "high frequency", "haute frequence": "high frequency",
    "surface équivalente radar": "radar cross section", "surface equivalente radar": "radar cross section",
    "ser": "radar cross section", "maillage": "mesh", "convergence": "convergence",
    "précision": "accuracy", "precision": "accuracy", "robustesse": "robustness",
    "représentativité": "representativeness", "representativite": "representativeness",
    "données synthétiques": "synthetic data", "donnees synthetiques": "synthetic data",
    "données réelles": "real data", "donnees reelles": "real data",
    "généralisation": "generalization", "generalisation": "generalization",
    "classification": "classification", "reconnaissance": "recognition",
    "apprentissage": "learning", "entraînement": "training", "entrainement": "training",
    "décalage de domaine": "domain shift", "decalage de domaine": "domain shift",
    "méthodes": "methods", "methodes": "methods", "méthode": "method", "methode": "method",
    "validité": "validity", "validite": "validity", "logiciel": "software",
    "prise en compte": "modeling", "prennent en compte": "model",
    "simulation": "simulation", "mesure": "measurement", "mesures": "measurements",
    "capteur": "sensor", "signal": "signal", "image": "image", "radar": "radar",
    "matériau": "material", "materiau": "material", "matériaux": "materials", "materiaux": "materials",
    "température": "temperature", "temperature": "temperature", "pression": "pressure",
    "écoulement": "flow", "ecoulement": "flow", "thermique": "thermal",
    "corrosion": "corrosion", "fatigue": "fatigue", "usure": "wear",
    "détection": "detection", "detection": "detection", "segmentation": "segmentation",
    "optimisation": "optimization", "modélisation": "modeling", "modelisation": "modeling",
}

_METHOD_WORDS = {
    "simulation", "solver", "solvers", "modeling", "modelling", "optimization", "learning",
    "training", "classification", "detection", "segmentation", "measurement", "experiment",
    "ray", "tracing", "optics", "asymptotic", "finite", "element", "mesh", "benchmark",
}
_PHENOMENON_WORDS = {
    "scattering", "diffraction", "convergence", "generalization", "shift", "robustness",
    "accuracy", "representativeness", "uncertainty", "noise", "fatigue", "corrosion", "wear",
    "thermal", "flow", "pressure", "temperature", "deformation", "failure", "stability",
}


def _norm(text: Any) -> str:
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9%/\-\s]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _unique(values: Iterable[str], limit: int) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        key = _norm(value)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _translate_phrases(text: str) -> str:
    out = " " + _norm(text) + " "
    for fr, en in sorted(_TRANSLATE.items(), key=lambda kv: len(kv[0]), reverse=True):
        pattern = r"(?<![a-z0-9])" + re.escape(_norm(fr)).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
        out = re.sub(pattern, en, out)
    return re.sub(r"\s+", " ", out).strip()


def _tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9][a-z0-9\-/]{2,}", _norm(text)) if t not in _STOP]


def _local_names(original_text: str) -> List[str]:
    names: List[str] = []
    for token in re.findall(r"\b[A-Z][A-Z0-9_-]{1,}\b|\b[A-Z][a-zA-Z0-9_-]{3,}\b", original_text or ""):
        nt = _norm(token)
        # Acronyms that have a generic scientific expansion are concepts, not local names.
        if nt in {_norm(k) for k in _TRANSLATE}:
            continue
        if nt not in _STOP:
            names.append(token)
    return _unique(names, 12)


def normalize_scientific_intent(intent: Dict[str, Any], evidence_text: str = "") -> Dict[str, Any]:
    """Return an enriched, generic intent suitable for scholarly retrieval."""
    out = dict(intent or {})
    title = str(out.get("verrou_title") or "")
    source = str(evidence_text or (out.get("source_basis") or {}).get("source_text_excerpt") or "")
    original = " ".join([title, source, str(out.get("scientific_problem") or "")])
    translated = _translate_phrases(original)

    local_names = _local_names(original)
    local_name_norms = {_norm(x) for x in local_names}
    toks = [t for t in _tokens(translated) if t not in local_name_norms]
    counts = Counter(toks)

    # Prefer multiword scientific expressions found in translated evidence.
    phrases: List[str] = []
    for n in (3, 2):
        for i in range(max(0, len(toks) - n + 1)):
            gram = toks[i:i+n]
            if any(x in _GENERIC for x in gram):
                continue
            if len(set(gram)) < n:
                continue
            phrases.append(" ".join(gram))
    phrase_counts = Counter(phrases)

    concepts = [p for p, _ in phrase_counts.most_common(10)]
    if len(concepts) < 4:
        concepts.extend([t for t, _ in counts.most_common(12)])
    concepts = _unique(concepts, 10)

    method_anchors = _unique(
        [p for p in concepts if any(w in _METHOD_WORDS for w in p.split())]
        + [t for t in toks if t in _METHOD_WORDS], 8
    )
    phenomenon_anchors = _unique(
        [p for p in concepts if any(w in _PHENOMENON_WORDS for w in p.split())]
        + [t for t in toks if t in _PHENOMENON_WORDS], 8
    )

    # Core concepts must be transferable and never depend on a local product name.
    core = _unique(
        [p for p in concepts if not any(_norm(name) in _norm(p) for name in local_names)]
        + method_anchors + phenomenon_anchors,
        8,
    )

    out["core_concepts"] = core
    out["method_anchors"] = method_anchors
    out["phenomenon_anchors"] = phenomenon_anchors
    out["project_tool_terms"] = local_names
    out["local_names"] = local_names
    out["normalized_research_text_en"] = translated[:1800]
    out["normalization_report"] = {
        "version": "generic_scientific_normalizer_v1",
        "project_specific_rules": False,
        "local_names_excluded_from_core": local_names,
        "core_concepts": core,
        "method_anchors": method_anchors,
        "phenomenon_anchors": phenomenon_anchors,
    }
    return out
