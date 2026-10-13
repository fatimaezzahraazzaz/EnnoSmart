# -*- coding: utf-8 -*-
"""Normalisation générique pour la reconstruction du système technique."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Iterable, List, Set


_STORAGE_HASH_RE = re.compile(r"_[0-9a-f]{10,}(?=\.[a-z0-9]{1,6}$)", re.I)
_EXT_RE = re.compile(r"\.(?:pdf|docx?|xlsx?|xlsm|pptx?|txt|csv|md|json)$", re.I)
TOKEN_RE = re.compile(r"[a-zA-ZÀ-ÿ][a-zA-ZÀ-ÿ0-9_-]{1,}|[A-Za-z]{1,8}\d{2,}[A-Za-z0-9_-]*")
REFERENCE_PATTERNS = [
    # Références alphanumériques compactes : AB123, ISO2768.
    re.compile(r"\b[A-Za-z]{2,8}\d{2,}[A-Za-z0-9_-]*\b"),
    # Références séparées uniquement lorsqu'elles sont écrites en majuscules.
    re.compile(r"\b[A-Z]{2,8}[-_]\d{2,}[A-Z0-9_-]*\b"),
]

# Termes documentaires ou transverses qui ne doivent jamais devenir seuls un
# objet technique. La liste est générique et ne contient aucun nom de projet.
GENERIC_WORDS: Set[str] = {
    "analyse", "annexe", "article", "bilan", "calcul", "comparaison",
    "compte", "conclusion", "configuration", "date", "detail", "details",
    "document", "donnee", "donnees", "dossier", "essai", "essais", "etat",
    "etude", "etudes", "exemple", "explication", "explications", "figure",
    "fichier", "general", "generale", "information", "introduction",
    "mesure", "mesures", "modele", "nouveau", "nouvelle", "objectif",
    "page", "partie", "plan", "plans", "projet", "rapport", "releve",
    "releves", "resultat", "resultats", "revision", "rev", "schema",
    "section", "simulation", "simulations", "synthese", "table", "tableau",
    "test", "tests", "version", "v1", "v2", "v3", "v4", "final",
    "avec", "dans", "des", "du", "de", "d", "la", "le", "les", "un",
    "une", "pour", "par", "sur", "sous", "entre", "vers", "sans", "apres",
    "avant", "plus", "moins", "ainsi", "dont", "lors", "aux", "et", "ou",
    "the", "and", "for", "with", "from", "into", "using", "study", "report",
    "analysis", "results", "result", "test", "tests", "new", "comparison",
    "etage", "fonction", "variation", "courbe", "courbes", "ancien", "ancienne",
    "est", "sont", "etre", "meme", "eventuel", "eventuell", "lequel", "laquelle",
    "alimente", "alimenter", "relev", "rev0", "rev1", "rev2", "rev3",
}

# Mots qui signalent qu'une expression nominale a de fortes chances de décrire
# un objet, composant, procédé, logiciel ou matériau technique. Cette liste est
# volontairement inter-domaines.
TECHNICAL_HEADS: Set[str] = {
    "algorithme", "architecture", "assemblage", "base", "batterie", "capteur",
    "circuit", "code", "composant", "controleur", "convertisseur",
    "dispositif", "electrode", "element", "ensemble", "filtre", "fluide",
    "interface", "logiciel", "materiau", "mecanisme", "methode", "modele",
    "module", "moteur", "pompe", "procede", "processeur", "protocole", "reacteur",
    "reseau", "reservoir", "service", "structure", "systeme", "traitement",
    "unite", "vanne", "application", "api", "pipeline", "modele", "dataset",
    "database", "sensor", "controller", "device", "filter", "mechanism",
    "material", "process", "reactor", "network", "software", "system", "module",
}

EVIDENCE_WORDS = {
    "analyse", "comparaison", "essai", "essais", "etude", "mesure", "mesures",
    "releve", "releves", "resultat", "resultats", "simulation", "simulations",
    "synthese", "test", "tests", "validation", "prototype", "prototypage",
}


def strip_accents(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def canonical_text(value: str) -> str:
    value = strip_accents(value).lower().replace("’", "'")
    value = re.sub(r"[^a-z0-9_-]+", " ", value)
    return normalize_space(value)


def clean_document_name(value: str) -> str:
    name = Path(str(value or "")).name
    name = _STORAGE_HASH_RE.sub("", name)
    name = _EXT_RE.sub("", name)
    name = re.sub(r"^\s*\d{3,}\s*[,;_-]?\s*", "", name)
    name = re.sub(r"\([^)]*(?:copie|copy)\s*\d*[^)]*\)", " ", name, flags=re.I)
    name = name.replace("_", " ")
    return normalize_space(name)


def singularize_token(token: str) -> str:
    token = canonical_text(token)
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 5 and token.endswith("aux"):
        return token[:-3] + "al"
    if len(token) > 5 and token.endswith("es"):
        return token[:-2]
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(value: str, *, keep_generic: bool = False) -> List[str]:
    tokens: List[str] = []
    for raw in TOKEN_RE.findall(canonical_text(value)):
        token = singularize_token(raw)
        if len(token) < 3 and not any(ch.isdigit() for ch in token):
            continue
        if re.fullmatch(r"(?:rev|version|v)\d+", token):
            continue
        if not keep_generic and token in GENERIC_WORDS:
            continue
        tokens.append(token)
    return tokens


def normalize_label(value: str) -> str:
    return " ".join(tokenize(value))


def extract_references(*values: str) -> List[str]:
    refs: Set[str] = set()
    rejected_prefixes = {
        "DE", "DES", "DU", "EN", "AU", "AUX", "LE", "LES", "LA", "UN", "UNE",
        "H", "I", "C", "F", "B", "DATE", "TYPE", "MASSE", "VITESSE", "EAU",
        "NIVEAU", "VALEUR", "COMPTER", "PENDANT", "MAXI", "ENV", "ETAGE",
    }
    for value in values:
        cleaned = clean_document_name(str(value or ""))
        for pattern in REFERENCE_PATTERNS:
            for raw in pattern.findall(cleaned):
                compact = re.sub(r"[-_ ]+", "", raw).upper()
                if re.fullmatch(r"[0-9A-F]{10,}", compact):
                    continue
                prefix_match = re.match(r"[A-Z]+", compact)
                prefix = prefix_match.group(0) if prefix_match else ""
                if prefix in rejected_prefixes:
                    continue
                # Une référence de stockage contenant un hash hexadécimal long
                # après un identifiant lisible est tronquée à sa partie stable.
                compact = re.sub(r"([A-Z]{2,8}\d{2,})[0-9A-F]{10,}$", r"\1", compact)
                refs.add(compact)
    return sorted(refs)


def distinctive_ngrams(value: str, *, min_n: int = 1, max_n: int = 4) -> List[str]:
    tokens = tokenize(value)
    out: List[str] = []
    for n in range(max(min_n, 1), min(max_n, len(tokens)) + 1):
        for index in range(0, len(tokens) - n + 1):
            gram = tokens[index:index + n]
            if not gram:
                continue
            if all(token in GENERIC_WORDS for token in gram):
                continue
            label = " ".join(gram)
            if label not in out:
                out.append(label)
    return out


def compact_unique(values: Iterable[str], limit: int = 20) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for value in values:
        normalized = normalize_space(value)
        key = canonical_text(normalized)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(normalized)
        if len(out) >= limit:
            break
    return out


def has_technical_head(label: str) -> bool:
    return bool(set(tokenize(label, keep_generic=True)) & TECHNICAL_HEADS)


def score_filename_phrase(label: str) -> float:
    tokens = tokenize(label)
    if not tokens:
        return 0.0
    score = 0.45
    score += min(0.30, 0.08 * len(tokens))
    if has_technical_head(label):
        score += 0.15
    if any(any(ch.isdigit() for ch in token) for token in tokens):
        score += 0.10
    return min(score, 1.0)
