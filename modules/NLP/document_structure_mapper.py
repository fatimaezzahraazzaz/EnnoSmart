# -*- coding: utf-8 -*-
"""
document_structure_mapper.py — V37

Idée centrale : ne plus analyser une phrase seule.
Chaque passage est rattaché à un contexte documentaire :
- titre parent
- chemin de section
- rôle probable de la section
- type de document

Ce module reste générique : il ne contient pas de filtre métier par domaine.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from .normalizer import normalize_text
from .cleaner import is_noise_line
from .document_type_classifier import enrich_document_type


ROLE_HINTS = {
    "objectif": [
        "objectif", "objectifs", "but", "finalite", "finalité", "performance a atteindre",
        "performances a atteindre", "ambition", "vise", "visé", "attendu",
        "objective", "objectives", "purpose", "aim", "aims", "goal", "goals", "research question"
    ],
    "verrou": [
        "verrou", "verrous", "question technique", "questions techniques", "interrogation technique",
        "incertitude", "difficulte", "difficulté", "limite", "limites", "probleme", "problème",
        "risque", "contrainte", "contraintes", "insuffisance", "insuffisances",
        "limitation", "limitations", "uncertainty", "uncertainties", "challenge", "challenges",
        "open problem", "open problems", "bottleneck", "bottlenecks"
    ],
    "etat_art": [
        "etat de l art", "état de l art", "bibliographie", "references", "références",
        "connaissances existantes", "solutions existantes", "litterature", "littérature", "article",
        "related work", "related works", "literature", "literature review", "prior work",
        "previous work", "background", "references", "bibliography", "state of the art"
    ],
    "methode": [
        "methode", "méthode", "methodologie", "méthodologie", "demarche", "démarche",
        "travaux", "protocole", "implementation", "implémentation", "architecture", "solution",
        "experience", "expérience", "essai", "essais", "simulation", "modelisation", "modélisation",
        "method", "methods", "methodology", "experimental setup", "materials and methods", "approach"
    ],
    "resultat": [
        "resultat", "résultat", "resultats", "résultats", "evaluation", "évaluation",
        "performance", "performances", "conclusion", "mesure", "mesures", "gain", "obtenu", "obtenus",
        "result", "results", "findings", "evaluation", "discussion", "experiments"
    ],
    "parametre": [
        "parametre", "paramètre", "parametres", "paramètres", "configuration", "seuil", "valeur",
        "pression", "debit", "débit", "temperature", "température", "taille", "dimension",
        "parameter", "parameters", "configuration", "configurations", "hyperparameter", "hyperparameters"
    ],
    "contribution": [
        "contribution", "avancee", "avancée", "innovation", "apport", "acquisition des connaissances",
        "connaissances acquises", "verrous leves", "verrous levés",
        "contribution", "contributions", "novelty", "originality", "main contributions"
    ],
}


def _norm(s: str) -> str:
    s = str(s or "").lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’", "aaaeeeeiioouuuc'")
    s = s.translate(tr)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _is_slide_marker(line: str) -> bool:
    return bool(re.match(r"^\[SLIDE(?:\s+\d+)?\]", line.strip(), flags=re.I))


def is_heading_line(line: str) -> bool:
    raw = str(line or "").strip()
    if not raw:
        return False
    low = _norm(raw)

    if len(raw) > 180:
        return False
    if is_noise_line(raw):
        return False

    # Numérotation type 1.2.3 Titre
    if re.match(r"^\d+(?:\.\d+){0,6}\s+\S", raw):
        return True

    # Marqueur slide
    if _is_slide_marker(raw):
        return True

    # Titre court finissant par :
    if raw.endswith(":") and len(raw) <= 120:
        return True

    # Titres très courts avec mots de structure
    if (
        len(raw) <= 90
        and not re.search(r"[.!?;]$", raw)
        and any(k in low for vals in ROLE_HINTS.values() for k in vals)
    ):
        # éviter de prendre une phrase complète comme titre
        if raw.count(".") <= 1 and len(re.findall(r"[A-Za-zÀ-ÿ]{3,}", raw)) <= 12:
            return True

    # ALL CAPS / casse titre
    letters = re.findall(r"[A-Za-zÀ-ÿ]", raw)
    if letters:
        uppers = sum(1 for c in letters if c.upper() == c)
        if len(raw) <= 80 and uppers / max(len(letters), 1) > 0.65 and len(letters) >= 5:
            return True

    return False


def section_role_hint(title: str, inherited: str = "unknown") -> str:
    t = _norm(title)
    for role, keys in ROLE_HINTS.items():
        if any(_norm(k) in t for k in keys):
            return role
    return inherited or "unknown"


def _heading_level(title: str) -> int:
    raw = str(title or "").strip()
    m = re.match(r"^(\d+(?:\.\d+){0,6})\s+", raw)
    if m:
        return 1 + m.group(1).count(".")
    if _is_slide_marker(raw):
        return 2
    return 2


def _split_text_lines(text: str) -> List[str]:
    text = str(text or "").replace("\r", "\n")
    lines = []
    for line in text.splitlines():
        line = normalize_text(line).strip()
        if not line:
            continue
        if is_noise_line(line):
            continue
        lines.append(line)
    return lines


def map_document_structure(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc = enrich_document_type(doc)
    lines = _split_text_lines(doc.get("text", ""))

    sections: List[Dict[str, Any]] = []
    stack: List[Dict[str, Any]] = []
    current = {
        "section_id": "sec_000",
        "section_title": "Document",
        "section_path": ["Document"],
        "section_level": 0,
        "section_role_hint": "unknown",
        "blocks": [],
    }
    sections.append(current)

    def push_heading(title: str):
        nonlocal current, stack, sections
        level = _heading_level(title)
        while stack and stack[-1]["section_level"] >= level:
            stack.pop()
        inherited = stack[-1].get("section_role_hint", "unknown") if stack else "unknown"
        hint = section_role_hint(title, inherited=inherited)
        path = [x["section_title"] for x in stack] + [title]
        sec = {
            "section_id": f"sec_{len(sections):03d}",
            "section_title": title,
            "section_path": path,
            "section_level": level,
            "section_role_hint": hint,
            "blocks": [],
        }
        sections.append(sec)
        stack.append(sec)
        current = sec

    for line in lines:
        if is_heading_line(line):
            push_heading(line)
        else:
            # Cas important : si le titre finit par "Verrou :" et le contenu suit après extraction,
            # on conserve le titre comme contexte du bloc suivant.
            current["blocks"].append(line)

    # Nettoyer sections vides sauf si elles portent un titre utile.
    cleaned = []
    for sec in sections:
        if sec.get("blocks") or sec.get("section_title") != "Document":
            s = dict(sec)
            # Contrat consommé par candidates.py. Les alias évitent de casser
            # les anciens consommateurs qui utilisent encore title/role_hint.
            s["title"] = s.get("section_title")
            s["role_hint"] = s.get("section_role_hint")
            s["content"] = " ".join(str(x) for x in s.get("blocks", []) if x)
            s["document"] = doc.get("document")
            s["document_type"] = doc.get("document_type")
            s["content_origin"] = doc.get("content_origin")
            cleaned.append(s)

    out = dict(doc)
    out["sections"] = cleaned
    return out


def map_documents_structure(documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [map_document_structure(d) for d in documents or []]


def context_prefix(item: Dict[str, Any]) -> str:
    """Texte injecté au modèle pour lui donner le contexte sans changer le passage source."""
    parts = []
    if item.get("document"):
        parts.append(f"Document: {item.get('document')}")
    if item.get("document_type"):
        parts.append(f"Type document: {item.get('document_type')}")
    if item.get("content_origin"):
        parts.append(f"Origine: {item.get('content_origin')}")
    if item.get("section_role_hint") and item.get("section_role_hint") != "unknown":
        parts.append(f"Rôle section: {item.get('section_role_hint')}")
    if item.get("section_title"):
        parts.append(f"Titre section: {item.get('section_title')}")
    return "\n".join(parts)
