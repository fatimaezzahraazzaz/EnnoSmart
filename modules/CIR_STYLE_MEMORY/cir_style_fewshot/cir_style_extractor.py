# -*- coding: utf-8 -*-
from __future__ import annotations

"""
cir_style_extractor.py

Phase 3 — Style Extractor

Rôle :
- lire style_memory_payload.json généré par cir_style_retriever.py ;
- analyser les exemples CIR Memory V2 ;
- extraire structure, vocabulaire, transitions, patterns de gap, conclusion ;
- produire un JSON intermédiaire exploitable par style_profile_builder.py.

Important :
- aucun appel LLM ici ;
- aucun fait scientifique nouveau ;
- Memory V2 reste style_only.
"""

import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


from .cir_style_retriever import (
    clean_text,
    truncate,
    _read_json,
    _write_json,
    style_memory_output_path,
)


# ============================================================
# Configuration
# ============================================================

STOPWORDS_FR = {
    "avec", "dans", "pour", "plus", "moins", "entre", "comme", "cette",
    "cela", "ainsi", "afin", "être", "etre", "sont", "nous", "notre",
    "leur", "leurs", "des", "les", "une", "aux", "sur", "par", "que",
    "qui", "quoi", "dont", "de", "du", "la", "le", "un", "en", "et",
    "ou", "au", "ce", "ces", "son", "ses", "il", "elle", "ils", "elles",
    "est", "ont", "été", "ete", "être", "avoir", "ces", "ses", "sa",
    "son", "se", "ne", "pas", "plus", "moins", "très", "tres", "aussi",
    "afin", "ainsi", "notamment", "projet", "travaux", "année", "annee",
    "cir", "page", "document", "classification", "confidentiel",
}

TRANSITION_MARKERS = [
    "en effet",
    "en outre",
    "par ailleurs",
    "de surcroît",
    "de surcroit",
    "en premier lieu",
    "en second lieu",
    "dans ce cadre",
    "à ce titre",
    "a ce titre",
    "ainsi",
    "cependant",
    "néanmoins",
    "neanmoins",
    "malgré",
    "malgre",
    "enfin",
    "d'autre part",
    "d’une part",
    "d'une part",
    "d’autre part",
    "il s’agit",
    "il s'agit",
]

GAP_MARKERS = [
    "reste limité",
    "restent limitées",
    "restent limités",
    "reste un problème ouvert",
    "problème ouvert",
    "probleme ouvert",
    "absence de",
    "il n’existe pas",
    "il n'existe pas",
    "pas de consensus",
    "limitation",
    "limites",
    "incertitude",
    "incertitudes",
    "non transposable",
    "non transférable",
    "non transferable",
    "nécessite",
    "necessite",
    "verrou",
    "complexe",
    "difficile",
    "malgré des résultats",
    "malgre des resultats",
    "insuffisant",
    "insuffisante",
    "insuffisantes",
]

CONCLUSION_MARKERS = [
    "en conclusion",
    "conclusion",
    "les travaux réalisés",
    "les travaux realises",
    "les travaux conduits",
    "ont permis",
    "ce projet a permis",
    "cette approche",
    "ces travaux",
    "sur le plan scientifique",
    "sur le plan technique",
    "sur le plan technologique",
    "nous pouvons résumer",
    "nous pouvons resumer",
    "en résumé",
    "en resume",
]

INTRO_MARKERS = [
    "l’objectif du présent projet",
    "l'objectif du présent projet",
    "l’objectif",
    "l'objectif",
    "de nombreux travaux",
    "la littérature",
    "l’état de l’art",
    "l'etat de l'art",
    "un verrou scientifique",
    "la finalité",
    "la finalite",
    "dans le cadre",
    "le projet",
]


NOISE_PATTERNS = [
    r"^\s*\[PAGE\s+\d+\]\s*$",
    r"[A-Z][A-Z0-9 &_-]{2,}\s*-\s*CONFIDENTIEL",
    r"Classification du document",
    r"Ce document est la propriété",
    r"Il ne peut être reproduit",
    r"Tous droits réservés",
    r"PAGE\s+\d+",
    r"\d+/\d+",
]


# ============================================================
# Helpers texte
# ============================================================

def normalize_for_match(text: Any) -> str:
    s = clean_text(text).lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’", "aaaeeeeiioouuuc'")
    s = s.translate(tr)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def is_noise_line(line: str) -> bool:
    low = clean_text(line)
    if not low:
        return True

    for pattern in NOISE_PATTERNS:
        if re.search(pattern, low, flags=re.IGNORECASE):
            return True

    return False


def clean_style_text(text: Any) -> str:
    raw = clean_text(text)
    if not raw:
        return ""

    lines = []
    for line in raw.splitlines():
        line = clean_text(line)
        if not line:
            continue
        if is_noise_line(line):
            continue
        lines.append(line)

    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    return cleaned.strip()


def split_paragraphs(text: Any) -> List[str]:
    text = clean_style_text(text)
    if not text:
        return []

    parts = re.split(r"\n{2,}", text)
    out = []

    for p in parts:
        p = clean_text(p)
        if len(p) >= 80:
            out.append(p)

    if not out and len(text) >= 80:
        out = [text]

    return out


def split_sentences(text: Any) -> List[str]:
    text = clean_style_text(text).replace("\n", " ")
    if not text:
        return []

    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÉÈÀÂÎÔÛÇ0-9])", text)
    out = []

    for p in parts:
        p = clean_text(p)
        if len(p) >= 35:
            out.append(p)

    return out


def first_sentences(text: Any, n: int = 2) -> List[str]:
    return split_sentences(text)[:n]


def last_sentences(text: Any, n: int = 2) -> List[str]:
    sents = split_sentences(text)
    return sents[-n:] if sents else []


def contains_any_marker(sentence: str, markers: List[str]) -> bool:
    low = normalize_for_match(sentence)
    return any(normalize_for_match(marker) in low for marker in markers)


def dedupe_strings(items: List[str], max_items: int = 20) -> List[str]:
    out = []
    seen = set()

    for item in items:
        item = clean_text(item)
        if not item:
            continue

        key = normalize_for_match(item)[:260]
        if key in seen:
            continue

        seen.add(key)
        out.append(item)

        if len(out) >= max_items:
            break

    return out


def word_tokens(text: Any) -> List[str]:
    s = normalize_for_match(text)
    tokens = re.findall(r"\b[a-zA-ZÀ-ÿ0-9_'-]{4,}\b", s)

    out = []
    for tok in tokens:
        tok = tok.strip("_-’'")
        if len(tok) < 4:
            continue
        if tok in STOPWORDS_FR:
            continue
        if tok.isdigit():
            continue
        out.append(tok)

    return out


def ngrams(tokens: List[str], n: int) -> List[str]:
    if len(tokens) < n:
        return []
    return [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


# ============================================================
# Extraction principale
# ============================================================

def extract_intro_patterns(examples: List[Dict[str, Any]]) -> List[str]:
    candidates = []

    for ex in examples:
        text = ex.get("text") or ""
        role = normalize_for_match(ex.get("style_role") or ex.get("role") or "")

        for sent in first_sentences(text, n=3):
            if contains_any_marker(sent, INTRO_MARKERS):
                candidates.append(sent)
            elif role in {"etat_art", "objectif", "verrou"}:
                candidates.append(sent)

    return dedupe_strings(candidates, max_items=12)


def extract_transition_patterns(examples: List[Dict[str, Any]]) -> List[str]:
    candidates = []

    for ex in examples:
        for sent in split_sentences(ex.get("text") or ""):
            if contains_any_marker(sent, TRANSITION_MARKERS):
                candidates.append(sent)

    return dedupe_strings(candidates, max_items=18)


def extract_gap_patterns(examples: List[Dict[str, Any]]) -> List[str]:
    candidates = []

    for ex in examples:
        role = normalize_for_match(ex.get("style_role") or ex.get("role") or "")

        for sent in split_sentences(ex.get("text") or ""):
            if contains_any_marker(sent, GAP_MARKERS):
                candidates.append(sent)
            elif role in {"verrou", "limite"} and len(sent) >= 80:
                candidates.append(sent)

    return dedupe_strings(candidates, max_items=18)


def extract_conclusion_patterns(examples: List[Dict[str, Any]]) -> List[str]:
    candidates = []

    for ex in examples:
        role = normalize_for_match(ex.get("style_role") or ex.get("role") or "")
        section_title = normalize_for_match(ex.get("section_title") or "")

        if role in {"contribution", "conclusion"} or "conclusion" in section_title:
            candidates.extend(first_sentences(ex.get("text") or "", n=3))
            candidates.extend(last_sentences(ex.get("text") or "", n=2))
            continue

        for sent in split_sentences(ex.get("text") or ""):
            if contains_any_marker(sent, CONCLUSION_MARKERS):
                candidates.append(sent)

    return dedupe_strings(candidates, max_items=14)


def extract_section_title_patterns(examples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counter = Counter()

    for ex in examples:
        title = clean_text(ex.get("section_title"))
        role = clean_text(ex.get("style_role") or ex.get("role"))
        if not title:
            continue
        counter[(role, title)] += 1

    out = []
    for (role, title), count in counter.most_common(20):
        out.append({
            "role": role,
            "section_title": title,
            "count": count,
        })

    return out


def infer_paragraph_order(examples: List[Dict[str, Any]]) -> List[str]:
    """
    Déduit un ordre rédactionnel à partir des rôles trouvés.
    Si les exemples sont incomplets, on renvoie un ordre CIR robuste.
    """

    role_counts = Counter()

    for ex in examples:
        role = normalize_for_match(ex.get("style_role") or ex.get("role") or "")
        if role:
            role_counts[role] += 1

    recommended = []

    if role_counts.get("objectif"):
        recommended.append("objectif et contexte du projet R&D")

    if role_counts.get("etat_art"):
        recommended.append("état de l’art et travaux existants")

    if role_counts.get("verrou"):
        recommended.append("limites de l’état de l’art et verrou scientifique")

    if role_counts.get("limite"):
        recommended.append("gap scientifique / non-transposabilité au cas projet")

    recommended.append("justification des travaux R&D nécessaires")

    if role_counts.get("contribution"):
        recommended.append("contribution scientifique, technique ou technologique")

    # Fallback complet
    if len(recommended) < 4:
        recommended = [
            "contexte scientifique du verrou",
            "travaux existants dans la littérature",
            "limites de l’état de l’art",
            "gap scientifique / non-transposabilité au cas projet",
            "justification des travaux R&D du projet",
            "transition vers la démarche expérimentale",
        ]

    return recommended


def extract_vocabulary(examples: List[Dict[str, Any]]) -> Dict[str, Any]:
    word_counter = Counter()
    bigram_counter = Counter()
    trigram_counter = Counter()

    by_role = defaultdict(Counter)

    for ex in examples:
        text = clean_style_text(ex.get("text") or "")
        role = clean_text(ex.get("style_role") or ex.get("role") or "style")

        tokens = word_tokens(text)

        word_counter.update(tokens)
        bigram_counter.update(ngrams(tokens, 2))
        trigram_counter.update(ngrams(tokens, 3))
        by_role[role].update(tokens)

    role_terms = {}
    for role, counter in by_role.items():
        role_terms[role] = [
            {"term": term, "count": count}
            for term, count in counter.most_common(20)
        ]

    return {
        "top_terms": [
            {"term": term, "count": count}
            for term, count in word_counter.most_common(35)
        ],
        "top_bigrams": [
            {"term": term, "count": count}
            for term, count in bigram_counter.most_common(25)
        ],
        "top_trigrams": [
            {"term": term, "count": count}
            for term, count in trigram_counter.most_common(20)
        ],
        "terms_by_role": role_terms,
    }


def compute_style_metrics(examples: List[Dict[str, Any]]) -> Dict[str, Any]:
    example_lengths = []
    paragraph_counts = []
    sentence_counts = []
    paragraph_lengths = []
    sentence_word_counts = []

    for ex in examples:
        text = clean_style_text(ex.get("text") or "")
        if not text:
            continue

        paragraphs = split_paragraphs(text)
        sentences = split_sentences(text)

        example_lengths.append(len(text))
        paragraph_counts.append(len(paragraphs))
        sentence_counts.append(len(sentences))

        for p in paragraphs:
            paragraph_lengths.append(len(p))

        for s in sentences:
            sentence_word_counts.append(len(word_tokens(s)))

    def avg(values: List[int | float]) -> float:
        return round(sum(values) / len(values), 2) if values else 0.0

    return {
        "examples_count": len(examples),
        "avg_chars_per_example": avg(example_lengths),
        "avg_paragraphs_per_example": avg(paragraph_counts),
        "avg_sentences_per_example": avg(sentence_counts),
        "avg_chars_per_paragraph": avg(paragraph_lengths),
        "avg_keywords_per_sentence": avg(sentence_word_counts),
        "min_chars_per_example": min(example_lengths) if example_lengths else 0,
        "max_chars_per_example": max(example_lengths) if example_lengths else 0,
    }


def role_distribution(examples: List[Dict[str, Any]]) -> Dict[str, int]:
    counter = Counter()

    for ex in examples:
        role = clean_text(ex.get("style_role") or ex.get("role") or "unknown")
        counter[role] += 1

    return dict(counter.most_common())


# ============================================================
# Extraction rhétorique avancée — EnnoScholar Pro
# ============================================================

MOVE_DEFINITIONS = {
    "introduire_contexte": {
        "label": "Introduire le contexte scientifique",
        "markers": ["l'objectif", "l’objectif", "de nombreux travaux", "la littérature", "l’état de l’art", "le projet", "un verrou scientifique"],
    },
    "decrire_principe": {
        "label": "Décrire un principe ou une famille de méthodes",
        "markers": ["repose sur", "se basent sur", "exploite", "utilise", "permet", "propose", "méthode", "technique", "approche"],
    },
    "comparer_ou_regrouper": {
        "label": "Comparer ou regrouper plusieurs travaux",
        "markers": ["une première", "une seconde", "d'une part", "d’autre part", "plusieurs", "par ailleurs", "en outre", "de surcroît"],
    },
    "formuler_limite": {
        "label": "Formuler une limite ou une insuffisance",
        "markers": ["malgré", "toutefois", "cependant", "néanmoins", "reste limité", "limites", "mitigés", "absence", "pas de consensus"],
    },
    "relier_au_projet": {
        "label": "Relier la limite au contexte projet",
        "markers": ["dans ce projet", "dans ce cadre", "contexte", "cas", "dossier", "conditions", "validation", "transposition"],
    },
    "justifier_rd": {
        "label": "Justifier la démarche R&D",
        "markers": ["il est nécessaire", "nécessite", "indispensable", "doit permettre", "afin de", "pour pouvoir", "travaux r&d"],
    },
}

REASONING_PATTERN_LIBRARY = [
    {
        "pattern_id": "family_principle_limit_project",
        "label": "Famille → principe → limite → lien projet",
        "steps": [
            "introduire la famille scientifique concernée",
            "décrire le principe technique mobilisé",
            "montrer l'apport méthodologique de cette famille",
            "formuler la limite commune observée dans la littérature",
            "relier cette limite au verrou du projet courant",
        ],
        "use_for_sections": ["travaux_existants_directement_lies", "limites_de_l_etat_de_l_art"],
    },
    {
        "pattern_id": "evidence_convergence_gap",
        "label": "Convergence des travaux → incertitude résiduelle → gap",
        "steps": [
            "regrouper les sources qui convergent vers un même mécanisme",
            "identifier ce que ces sources permettent déjà d'établir",
            "identifier ce qui n'est pas démontré dans le contexte du projet",
            "formuler l'incertitude technique restante",
            "justifier la validation expérimentale propre au dossier",
        ],
        "use_for_sections": ["demonstration_que_le_verrou_reste_non_resolu", "gap_scientifique_technique"],
    },
    {
        "pattern_id": "related_method_transposition",
        "label": "Travaux connexes → éclairage méthodologique → prudence de transposition",
        "steps": [
            "présenter les travaux connexes comme éclairage méthodologique",
            "extraire uniquement le principe transposable",
            "éviter de survaloriser le domaine applicatif éloigné",
            "formuler les risques de transposition",
            "ramener la discussion au verrou du projet courant",
        ],
        "use_for_sections": ["travaux_connexes_ou_methodes_transposables"],
    },
    {
        "pattern_id": "rd_need_from_validation_missing",
        "label": "Validation absente → protocole nécessaire → travaux R&D",
        "steps": [
            "rappeler la connaissance disponible",
            "montrer que la validation reste incomplète",
            "identifier les paramètres ou critères à objectiver",
            "définir la nécessité d'un protocole propre au projet",
            "conclure sur la justification CIR des travaux R&D",
        ],
        "use_for_sections": ["synthese_cir_exploitable", "gap_scientifique_technique"],
    },
]

COMPARISON_PATTERN_LIBRARY = [
    {
        "pattern_id": "regroupement_par_familles",
        "label": "Regroupement par familles d'approches",
        "markers": ["une première famille", "une seconde famille", "plusieurs familles", "famille d'approches"],
        "writer_instruction": "Regrouper les sources selon le mécanisme scientifique plutôt que selon l'ordre des articles.",
    },
    {
        "pattern_id": "complementarite_methodologique",
        "label": "Complémentarité méthodologique",
        "markers": ["complètent", "complémentaire", "par ailleurs", "en outre"],
        "writer_instruction": "Montrer comment plusieurs travaux éclairent des dimensions différentes du même verrou.",
    },
    {
        "pattern_id": "opposition_limite_apport",
        "label": "Apport connu mais limite persistante",
        "markers": ["mais", "toutefois", "cependant", "néanmoins", "malgré"],
        "writer_instruction": "Équilibrer l'apport des travaux et la limite qui empêche une transposition directe.",
    },
    {
        "pattern_id": "progression_validation",
        "label": "Progression vers le besoin de validation",
        "markers": ["en premier lieu", "en second lieu", "enfin"],
        "writer_instruction": "Organiser le raisonnement vers la nécessité d'un protocole d'évaluation propre au dossier.",
    },
]

PARAGRAPH_BLUEPRINTS = {
    "positionnement_scientifique_du_verrou": {
        "paragraph_role": "situer le verrou avant les articles",
        "moves": ["introduire_contexte", "formuler_limite", "relier_au_projet"],
        "logic": "problématique scientifique → incertitude → enjeu projet",
        "avoid": ["démarrer par une citation", "énumérer les articles"],
    },
    "travaux_existants_directement_lies": {
        "paragraph_role": "présenter les familles directes",
        "moves": ["decrire_principe", "comparer_ou_regrouper", "formuler_limite"],
        "logic": "famille scientifique → principe → apport → limite commune",
        "avoid": ["fiche article", "titre d'article comme sujet grammatical"],
    },
    "travaux_connexes_ou_methodes_transposables": {
        "paragraph_role": "mobiliser les travaux connexes avec prudence",
        "moves": ["decrire_principe", "formuler_limite", "relier_au_projet"],
        "logic": "éclairage méthodologique → prudence → transposition limitée",
        "avoid": ["décrire longuement le domaine éloigné", "présenter comme preuve centrale"],
    },
    "limites_de_l_etat_de_l_art": {
        "paragraph_role": "synthétiser les insuffisances",
        "moves": ["formuler_limite", "comparer_ou_regrouper", "relier_au_projet"],
        "logic": "limites communes → conséquences projet → incertitude restante",
        "avoid": ["répétition article par article", "limite vague"],
    },
    "gap_scientifique_technique_justifiant_les_travaux_rd": {
        "paragraph_role": "formuler le gap défendable CIR",
        "moves": ["formuler_limite", "relier_au_projet", "justifier_rd"],
        "logic": "écart littérature/projet → validation manquante → travaux R&D",
        "avoid": ["gap générique", "absence de contrainte projet"],
    },
    "synthese_cir_exploitable": {
        "paragraph_role": "conclure sur la nécessité R&D",
        "moves": ["relier_au_projet", "justifier_rd"],
        "logic": "connaissances disponibles → incertitude résiduelle → protocole propre au dossier",
        "avoid": ["conclusion promotionnelle", "affirmation de réussite"],
    },
}


def extract_scientific_moves(examples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Détecte les mouvements rhétoriques observés dans les exemples de style."""
    counters = Counter()
    example_sentences = defaultdict(list)

    for ex in examples:
        for sent in split_sentences(ex.get("text") or ""):
            low = normalize_for_match(sent)
            for move_id, spec in MOVE_DEFINITIONS.items():
                if any(normalize_for_match(m) in low for m in spec.get("markers", [])):
                    counters[move_id] += 1
                    if len(example_sentences[move_id]) < 3:
                        example_sentences[move_id].append(truncate(anonymize_style_signal(sent), 260))

    moves = []
    for move_id, spec in MOVE_DEFINITIONS.items():
        moves.append({
            "move_id": move_id,
            "label": spec.get("label"),
            "observed_count": counters.get(move_id, 0),
            "example_style_signals": example_sentences.get(move_id, []),
            "usage": "rhetorical_structure_only",
        })
    return moves


def anonymize_style_signal(text: Any) -> str:
    """Nettoyage léger pour éviter de réinjecter des faits historiques."""
    s = clean_text(text)
    if not s:
        return ""
    # Remplace les années, identifiants propres et citations bibliographiques.
    s = re.sub(r"\b20(1[5-9]|2[0-9]|3[0-5])\b", "[année]", s)
    s = re.sub(
        r"\b(?=[A-Z0-9_-]{3,}\b)(?=[A-Z0-9_-]*[A-Z])[A-Z][A-Z0-9_-]*\b",
        "[identifiant]",
        s,
    )
    s = re.sub(r"\([A-ZÉÈÀÂÎÔÛÇ][A-Za-zÀ-ÿ'\-]+\s+et\s+al\.?\)\s*\d*", "([auteurs])", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_comparison_patterns(examples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Combine patterns canoniques et signaux observés."""
    observed = []
    for pattern in COMPARISON_PATTERN_LIBRARY:
        count = 0
        examples_found = []
        for ex in examples:
            for sent in split_sentences(ex.get("text") or ""):
                low = normalize_for_match(sent)
                if any(normalize_for_match(m) in low for m in pattern.get("markers", [])):
                    count += 1
                    if len(examples_found) < 2:
                        examples_found.append(truncate(anonymize_style_signal(sent), 260))
        p = dict(pattern)
        p["observed_count"] = count
        p["example_style_signals"] = examples_found
        p["usage"] = "comparison_structure_only"
        observed.append(p)
    return observed


def extract_reasoning_patterns(examples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Retourne des schémas de raisonnement stables enrichis par des compteurs d'observation."""
    all_text = normalize_for_match(" ".join(ex.get("text") or "" for ex in examples))
    patterns = []
    for p in REASONING_PATTERN_LIBRARY:
        score = 0
        for step in p.get("steps", []):
            # On compte des marqueurs génériques du step, pas le contenu métier.
            tokens = [t for t in re.findall(r"\b[a-zA-ZÀ-ÿ]{5,}\b", normalize_for_match(step)) if t not in STOPWORDS_FR]
            if any(tok in all_text for tok in tokens[:4]):
                score += 1
        item = dict(p)
        item["observed_signal_score"] = score
        item["usage"] = "reasoning_structure_only"
        patterns.append(item)
    return patterns


def build_paragraph_blueprints(examples: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Blueprints déterministes pour Phase 5, avec signaux d'observation."""
    moves = extract_scientific_moves(examples)
    move_counts = {m["move_id"]: m.get("observed_count", 0) for m in moves}
    out = {}
    for key, bp in PARAGRAPH_BLUEPRINTS.items():
        item = dict(bp)
        item["observed_move_counts"] = {m: move_counts.get(m, 0) for m in bp.get("moves", [])}
        item["usage"] = "paragraph_structure_only"
        out[key] = item
    return out


# ============================================================
# API publique
# ============================================================

def style_extraction_output_path(organisme: str, project: str, year: str) -> Path:
    return style_memory_output_path(organisme, project, year).parent / "style_extraction_payload.json"


def extract_style_from_memory_payload(
    organisme: str,
    project: str,
    year: str,
    style_memory_payload_path: Optional[str | Path] = None,
    output_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """
    Lit style_memory_payload.json et produit style_extraction_payload.json.
    """

    input_path = (
        Path(style_memory_payload_path)
        if style_memory_payload_path
        else style_memory_output_path(organisme, project, year)
    )

    out_path = (
        Path(output_path)
        if output_path
        else style_extraction_output_path(organisme, project, year)
    )

    payload = _read_json(input_path, {}) or {}

    examples = payload.get("style_memory") or payload.get("style_examples") or []
    examples = [ex for ex in examples if isinstance(ex, dict)]

    if not examples:
        result = {
            "ok": False,
            "phase": "phase_3_dynamic_fewshot_style",
            "step": "cir_style_extractor",
            "status": "empty_style_memory",
            "message": "Aucun exemple style_memory trouvé. Lance d'abord cir_style_retriever.py.",
            "input_path": str(input_path),
            "output_path": str(out_path),
            "style_extraction": {},
            "memory_as_proof": False,
        }
        _write_json(out_path, result)
        return result

    intro_patterns = extract_intro_patterns(examples)
    transition_patterns = extract_transition_patterns(examples)
    gap_patterns = extract_gap_patterns(examples)
    conclusion_patterns = extract_conclusion_patterns(examples)

    extraction = {
        "paragraph_order": infer_paragraph_order(examples),
        "intro_patterns": [
            truncate(x, 450) for x in intro_patterns
        ],
        "transition_patterns": [
            truncate(x, 450) for x in transition_patterns
        ],
        "gap_patterns": [
            truncate(x, 500) for x in gap_patterns
        ],
        "conclusion_patterns": [
            truncate(x, 500) for x in conclusion_patterns
        ],
        "section_title_patterns": extract_section_title_patterns(examples),
        "vocabulary": extract_vocabulary(examples),
        "metrics": compute_style_metrics(examples),
        "role_distribution": role_distribution(examples),
        "tone_signals": [
            "consultant CIR",
            "scientifique",
            "prudent",
            "argumentatif",
            "non promotionnel",
            "centré sur les limites de l’état de l’art",
            "centré sur la justification des travaux R&D",
        ],
        "scientific_moves": extract_scientific_moves(examples),
        "reasoning_patterns": extract_reasoning_patterns(examples),
        "comparison_patterns": extract_comparison_patterns(examples),
        "paragraph_blueprints": build_paragraph_blueprints(examples),
    }

    result = {
        "ok": True,
        "phase": "phase_3_dynamic_fewshot_style",
        "step": "cir_style_extractor",
        "payload_type": "style_extraction_payload_v2_reasoning_patterns",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "organisme": organisme,
        "project": project,
        "year": str(year),
        "input_path": str(input_path),
        "examples_count": len(examples),
        "style_extraction": extraction,
        "rules": {
            "usage": "style_only",
            "memory_as_proof": False,
            "can_be_cited": False,
            "scientific_sources_allowed": "article_cards_only",
        },
        "output_path": str(out_path),
    }

    _write_json(out_path, result)
    return result
