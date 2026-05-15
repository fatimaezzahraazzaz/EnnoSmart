"""
modules/NLP/normalizer.py — VERSION RÉDUITE (universelle)
──────────────────────────────────────────────────────────────────────────────
Normalisation de la terminologie R&D / CIR après nettoyage (cleaner.py).

  cleaner.py ──► normalizer.py ──► segmenter.py ──► evidence_mapper.py

CE QUI A CHANGÉ par rapport à l'ancienne version :
  - SUPPRIMÉ : TECH_CANONICAL_TERMS (SCoT4UT, Defects4J, HumanEval, JUnit,
    GPT-3.5-Turbo, RAG4UT, métriques logicielles...).
    → C'était du vocabulaire SCALIAN/IA codé en dur. Inutile et nuisible
      pour un dossier emballage médical, militaire, chimie, etc.
  - SUPPRIMÉ : _normalize_technical_terms() et son appel.
  - CONSERVÉ : uniquement ce qui est VRAIMENT universel et non lié à un
    domaine technique :
        * terminologie CIR pure (CIR, CII, R&D, dépenses de R&D...)
        * concepts R&D structurants (état de l'art, verrou, démarche...)
        * organismes officiels (BpiFrance, ANR, ANRT, CIFRE, DRFIP)
        * niveaux TRL
        * unités financières (k€, M€)
        * ETP
        * anglicismes R&D génériques (deliverables, milestones...)
        * variantes orthographiques courantes

Philosophie inchangée : NON-DESTRUCTIVE — on unifie, on ne supprime rien.

Version : 2.0.0 (réduite, universelle)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# DICTIONNAIRES DE NORMALISATION — UNIQUEMENT UNIVERSELS
# ══════════════════════════════════════════════════════════════════════════════

# ── 1. Terminologie CIR / dispositifs fiscaux ─────────────────────────────────
# Universelle : tout dossier CIR, quel que soit le domaine, utilise ces termes.
CIR_TERMINOLOGY: dict[str, list[str]] = {
    "CIR (crédit d'impôt recherche)": [
        r"crédit\s+d['']\s*impôt\s+(?:pour\s+la\s+)?recherche",
        r"crédit\s+impôt\s+recherche",
        r"credit\s+impot\s+recherche",
        r"\bcrédit\s+impôt\b",
        r"\bC\.I\.R\b",
    ],
    "CII (crédit d'impôt innovation)": [
        r"crédit\s+d['']\s*impôt\s+(?:pour\s+l['']\s*)?innovation",
        r"crédit\s+impôt\s+innovation",
        r"\bC\.I\.I\b",
    ],
    "R&D (recherche et développement)": [
        r"recherche\s+et\s+d[ée]veloppement",
        r"recherche\s+&\s+d[ée]veloppement",
        r"activit[ée]s?\s+de\s+R(?:echerche)?\s*[&et]+\s*D(?:[ée]veloppement)?",
        r"\bR\s*-\s*D\b",
        r"\bR\.\s*D\.\b",
        r"\bR\s*et\s*D\b",
    ],
    "dépenses de R&D": [
        r"d[ée]penses?\s+(?:de\s+)?recherche\s+et\s+d[ée]veloppement",
        r"co[uû]ts?\s+(?:de\s+)?R&D",
        r"charges?\s+(?:de\s+)?R&D",
        r"d[ée]penses?\s+(?:de\s+)?R(?:echerche)?\s*[&et]+\s*D",
    ],
    "base de calcul CIR": [
        r"assiette\s+(?:du\s+)?CIR",
        r"base\s+(?:de\s+calcul\s+)?(?:du\s+)?CIR",
        r"base\s+(?:des\s+)?d[ée]penses\s+[ée]ligibles",
    ],
    "sous-traitance agréée": [
        r"sous[\s-]traitance\s+(?:de\s+)?R&D",
        r"prestataire\s+(?:de\s+)?R&D",
        r"organisme\s+(?:de\s+)?recherche\s+agr[ée][ée]",
        r"sous[\s-]traitant\s+agr[ée][ée]",
    ],
    "dépenses de personnel R&D": [
        r"d[ée]penses?\s+(?:de\s+)?personnel\s+(?:de\s+)?(?:R&D|recherche)",
        r"salaires?\s+(?:des?\s+)?chercheurs?",
        r"r[ée]mun[ée]rations?\s+(?:du\s+)?personnel\s+(?:de\s+)?recherche",
        r"masse\s+salariale\s+R&D",
    ],
    "dotations aux amortissements": [
        r"amortissements?\s+(?:des?\s+)?(?:immobilisations?\s+)?(?:corporelles?|incorporelles?)",
        r"dotations?\s+(?:aux\s+)?amortissements?",
        r"d[ée]pr[ée]ciation\s+(?:des?\s+)?actifs?",
    ],
    "frais de fonctionnement": [
        r"frais?\s+(?:de\s+)?fonctionnement",
        r"charges?\s+(?:de\s+)?fonctionnement",
        r"frais?\s+(?:g[ée]n[ée]raux?|annexes?)\s+R&D",
    ],
}

# ── 2. Concepts R&D structurants ──────────────────────────────────────────────
# Universels : ce sont les briques du raisonnement CIR, indépendantes du domaine.
RD_CONCEPTS: dict[str, list[str]] = {
    "état de l'art": [
        r"[ée]tat\s+de\s+l['']\s*art",
        r"ETAT\s+DE\s+L['']\s*ART",
        r"prior\s+art",
        r"state\s+of\s+the\s+art",
        r"etat\s+art",
    ],
    "verrou technologique": [
        r"verrous?\s+technologiques?",
        r"verrous?\s+techniques?",
        r"blocages?\s+technologiques?",
        r"obstacles?\s+technologiques?",
        r"technical\s+barriers?",
        r"technological\s+challenges?",
        r"lock\s+technologique",
    ],
    "incertitude scientifique": [
        r"incertitudes?\s+(?:scientifiques?|techniques?|technologiques?)",
        r"al[ée]as?\s+(?:scientifiques?|techniques?)",
        r"risques?\s+(?:scientifiques?|techniques?)",
        r"scientific\s+uncertainty",
    ],
    "démarche scientifique": [
        r"d[ée]marche\s+(?:scientifique|exp[ée]rimentale|de\s+recherche)",
        r"approche\s+(?:scientifique|exp[ée]rimentale)",
        r"m[ée]thodologie\s+(?:de\s+)?recherche",
        r"protocole\s+(?:exp[ée]rimental|de\s+recherche)",
    ],
    "résultats de R&D": [
        r"r[ée]sultats?\s+(?:des?\s+)?(?:travaux\s+(?:de\s+)?)?R(?:echerche)?(?:\s*[&et]+\s*D(?:[ée]veloppement)?)?",
        r"r[ée]sultats?\s+(?:obtenus?|attendus?|escompt[ée]s?)",
        r"livrables?\s+R&D",
        r"outputs?\s+(?:de\s+)?R&D",
    ],
    "travaux de R&D": [
        r"travaux\s+(?:de\s+)?(?:R(?:echerche)?\s*[&et]+\s*D(?:[ée]veloppement)?|recherche)",
        r"activit[ée]s?\s+(?:de\s+)?R&D",
        r"programmes?\s+(?:de\s+)?recherche",
        r"projets?\s+(?:de\s+)?R&D",
        r"R&D\s+activities",
    ],
    "innovation technologique": [
        r"innovations?\s+technologiques?",
        r"innovations?\s+(?:de\s+)?proc[ée]d[ée]",
        r"innovations?\s+(?:de\s+)?produit",
        r"technological\s+innovation",
    ],
    "propriété intellectuelle": [
        r"propri[ée]t[ée]\s+intellectuelle",
        r"droits?\s+(?:de\s+)?propri[ée]t[ée]\s+intellectuelle",
        r"intellectual\s+property",
    ],
}

# ── 3. Organismes officiels (formes canoniques) ───────────────────────────────
# Universels : organismes du dispositif CIR français, présents dans tout dossier.
ORGANISMS: dict[str, list[str]] = {
    "BpiFrance": [
        r"Bpi\s*France",
        r"BPI\s*France",
        r"banque\s+publique\s+d['']\s*investissement",
        r"B\.P\.I\.",
    ],
    "ANRT (Association Nationale Recherche Technologie)": [
        r"A\.N\.R\.T\.",
        r"association\s+nationale\s+(?:de\s+la\s+)?recherche\s+(?:et\s+(?:de\s+la\s+)?)?technologie",
    ],
    "ANR (Agence Nationale de la Recherche)": [
        r"A\.N\.R\.",
        r"agence\s+nationale\s+(?:de\s+la\s+)?recherche",
    ],
    "CIFRE (Convention Industrielle de Formation par la Recherche)": [
        r"convention\s+industrielle\s+(?:de\s+)?formation\s+(?:par\s+la\s+)?recherche",
        r"allocations?\s+CIFRE",
        r"doctorat\s+CIFRE",
    ],
    "DRFIP": [
        r"D\.R\.F\.I\.P\.",
        r"direction\s+r[ée]gionale\s+(?:des?\s+)?finances?\s+publiques?",
    ],
}

# ── 4. Niveaux TRL (Technology Readiness Level) — universel ───────────────────
TRL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bTRL\s*[-–]?\s*([1-9])\b", re.IGNORECASE), r"TRL \1"),
    (re.compile(r"\bniveau\s+(?:de\s+)?maturit[ée]\s+technologique\s+([1-9])\b", re.IGNORECASE), r"TRL \1"),
    (re.compile(r"\bNMT\s*([1-9])\b", re.IGNORECASE), r"TRL \1"),
]

# ── 5. Unités financières — universel ─────────────────────────────────────────
FINANCIAL_UNITS: list[tuple[re.Pattern, object]] = [
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*[kK][€Ee][Uu][Rr]?"), lambda m: f"{_expand_k(m.group(1))} €"),
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*[kK]\s*€"),            lambda m: f"{_expand_k(m.group(1))} €"),
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*[Mm][€Ee][Uu][Rr]?"), lambda m: f"{_expand_m(m.group(1))} €"),
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*[Mm]\s*€"),            lambda m: f"{_expand_m(m.group(1))} €"),
    (re.compile(r"(\d+)\s+milliers?\s+d['']\s*euros?", re.IGNORECASE),
     lambda m: f"{int(m.group(1)) * 1000:,} €".replace(",", " ")),
    (re.compile(r"(\d+(?:[.,]\d+)?)\s+millions?\s+d['']\s*euros?", re.IGNORECASE),
     lambda m: f"{_expand_m(m.group(1))} €"),
]


def _expand_k(val: str) -> str:
    try:
        n = float(val.replace(",", "."))
        return f"{int(n * 1000):,}".replace(",", " ")
    except ValueError:
        return val


def _expand_m(val: str) -> str:
    try:
        n = float(val.replace(",", "."))
        return f"{int(n * 1_000_000):,}".replace(",", " ")
    except ValueError:
        return val


# ── 6. ETP — universel ────────────────────────────────────────────────────────
ETP_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b[ée]quivalent[s]?\s*[–-]?\s*temps[\s-]?plein\b", re.IGNORECASE), "ETP"),
    (re.compile(r"\bFTE\b"), "ETP"),
    (re.compile(r"\bfull[\s-]?time\s+equivalent\b", re.IGNORECASE), "ETP"),
    (re.compile(r"\bE\.T\.P\.\b"), "ETP"),
]

# ── 7. Anglicismes R&D génériques (si texte FR) — universel ───────────────────
ANGLICISMS: dict[str, list[str]] = {
    "livrables": [
        r"\bdeliverables?\b",
        r"\boutputs?\b(?!\s+(?:de|du|des))",
    ],
    "jalons": [
        r"\bmilestones?\b",
        r"\bcheckpoints?\b(?!\s+(?:de|du))",
    ],
    "feuille de route": [
        r"\broadmaps?\b",
        r"\btech\s+roadmap\b",
    ],
    "cahier des charges": [
        r"\bspecifications?\s+document\b",
        r"\brequirements?\s+(?:document|specification)\b",
    ],
    "preuve de concept": [
        r"\bproof[\s-]of[\s-]concept\b",
        r"\bPoC\b",
    ],
    "prototype": [
        r"\bmockup\b",
    ],
}

# ── 8. Variantes orthographiques techniques courantes — universel ─────────────
ORTHOGRAPHIC_VARIANTS: dict[str, list[str]] = {
    "algorithme": [r"algorythme"],
    "paramètre":  [r"param[eè]tre\b"],
    "modèle":     [r"mod[eè]le\b"],
    "données":    [r"donn[eé]es?\b"],
    "système":    [r"syst[eè]me\b"],
    "méthode":    [r"m[eé]thode\b"],
    "réseau":     [r"r[eé]seau\b"],
    "mémoire":    [r"m[eé]moire\b"],
}


# ══════════════════════════════════════════════════════════════════════════════
# DÉTECTION DE LANGUE
# ══════════════════════════════════════════════════════════════════════════════

_FR_MARKERS = re.compile(
    r"\b(?:le|la|les|un|une|des|du|de|et|est|sont|pour|avec|dans|sur|"
    r"par|au|aux|qui|que|se|il|elle|nous|vous|ils|elles|je|tu|"
    r"recherche|développement|projet|résultats?|travaux|rapport)\b",
    re.IGNORECASE,
)

_EN_MARKERS = re.compile(
    r"\b(?:the|a|an|of|and|is|are|for|with|in|on|by|at|to|"
    r"we|they|our|this|that|which|has|have|been|research|"
    r"development|project|results?|report)\b",
    re.IGNORECASE,
)


def _detect_language(text: str) -> str:
    """Retourne 'fr' | 'en' | 'mixed'. Défaut FR pour les dossiers CIR."""
    sample = text[:2000]
    fr_count = len(_FR_MARKERS.findall(sample))
    en_count = len(_EN_MARKERS.findall(sample))
    if fr_count == 0 and en_count == 0:
        return "fr"
    if fr_count >= en_count * 1.5:
        return "fr"
    if en_count >= fr_count * 1.5:
        return "en"
    return "mixed"


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class NormalizedChunk:
    original: str
    normalized: str
    language: str
    substitutions: list[tuple[str, str]]
    substitution_count: int


@dataclass
class NormalizerResult:
    chunks: list[NormalizedChunk]
    normalized_chunks: list[str]
    total_substitutions: int
    substitution_types: dict[str, int]


# ══════════════════════════════════════════════════════════════════════════════
# FONCTIONS DE NORMALISATION
# ══════════════════════════════════════════════════════════════════════════════

def _apply_dict_normalizations(
    text: str,
    dictionary: dict[str, list[str]],
    category: str,
    substitutions: list[tuple[str, str]],
) -> str:
    """Applique un dictionnaire de normalisation (remplacement simple)."""
    for canonical, patterns in dictionary.items():
        for pattern in patterns:
            try:
                compiled = re.compile(pattern, re.IGNORECASE | re.UNICODE)
                new_text = compiled.sub(canonical, text)
                if new_text != text:
                    substitutions.append((f"{category}: {pattern[:40]}", canonical))
                    text = new_text
            except re.error as exc:
                logger.debug("Pattern invalide dans %s : %s — %s", category, pattern, exc)
    return text


def _normalize_trl(text: str, substitutions: list) -> str:
    for pattern, replacement in TRL_PATTERNS:
        new_text = pattern.sub(replacement, text)
        if new_text != text:
            substitutions.append(("TRL", f"→ {replacement}"))
            text = new_text
    return text


def _normalize_financial(text: str, substitutions: list) -> str:
    for pattern, replacement in FINANCIAL_UNITS:
        new_text = pattern.sub(replacement, text)
        if new_text != text:
            substitutions.append(("financial", "k€/M€ → montant expansé"))
            text = new_text
    return text


def _normalize_etp(text: str, substitutions: list) -> str:
    for pattern, replacement in ETP_PATTERNS:
        new_text = pattern.sub(replacement, text)
        if new_text != text:
            substitutions.append(("ETP", f"→ {replacement}"))
            text = new_text
    return text


def _normalize_anglicisms(text: str, language: str, substitutions: list) -> str:
    if language not in ("fr", "mixed"):
        return text
    for canonical, patterns in ANGLICISMS.items():
        for pattern in patterns:
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
                new_text = compiled.sub(canonical, text)
                if new_text != text:
                    substitutions.append(("anglicism", f"→ {canonical}"))
                    text = new_text
            except re.error:
                pass
    return text


def _normalize_orthography(text: str, substitutions: list) -> str:
    for canonical, patterns in ORTHOGRAPHIC_VARIANTS.items():
        for pattern in patterns:
            try:
                compiled = re.compile(pattern, re.IGNORECASE)

                def _case_replace(m: re.Match, canon: str = canonical) -> str:
                    orig = m.group(0)
                    return canon.capitalize() if orig[:1].isupper() else canon

                new_text = compiled.sub(_case_replace, text)
                if new_text != text:
                    substitutions.append(("orthography", f"→ {canonical}"))
                    text = new_text
            except re.error:
                pass
    return text


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def normalize_chunk(text: str) -> NormalizedChunk:
    """
    Normalise un chunk de texte nettoyé.

    Applique dans l'ordre (toutes les étapes sont UNIVERSELLES) :
      1. Détection de langue
      2. Terminologie CIR
      3. Concepts R&D structurants
      4. Organismes officiels
      5. Niveaux TRL
      6. Unités financières
      7. ETP
      8. Anglicismes R&D génériques (si texte FR)
      9. Variantes orthographiques

    PLUS de normalisation de termes techniques spécifiques à un domaine.
    """
    if not text or not text.strip():
        return NormalizedChunk(
            original=text,
            normalized=text,
            language="fr",
            substitutions=[],
            substitution_count=0,
        )

    language = _detect_language(text)
    substitutions: list[tuple[str, str]] = []
    current = text

    current = _apply_dict_normalizations(current, CIR_TERMINOLOGY, "CIR", substitutions)
    current = _apply_dict_normalizations(current, RD_CONCEPTS, "RD_CONCEPT", substitutions)
    current = _apply_dict_normalizations(current, ORGANISMS, "ORGANISM", substitutions)
    current = _normalize_trl(current, substitutions)
    current = _normalize_financial(current, substitutions)
    current = _normalize_etp(current, substitutions)
    current = _normalize_anglicisms(current, language, substitutions)
    current = _normalize_orthography(current, substitutions)

    return NormalizedChunk(
        original=text,
        normalized=current,
        language=language,
        substitutions=substitutions,
        substitution_count=len(substitutions),
    )


def normalize_chunks(chunks: list[str]) -> NormalizerResult:
    """Normalise un lot de chunks nettoyés."""
    normalized_list: list[NormalizedChunk] = []
    substitution_types: dict[str, int] = {}
    total = 0

    for chunk in chunks:
        nc = normalize_chunk(chunk)
        normalized_list.append(nc)
        total += nc.substitution_count
        for category, _ in nc.substitutions:
            cat_key = category.split(":")[0].strip()
            substitution_types[cat_key] = substitution_types.get(cat_key, 0) + 1

    logger.info(
        "Normalisation : %d chunks | %d substitutions | types=%s",
        len(chunks), total, substitution_types,
    )

    return NormalizerResult(
        chunks=normalized_list,
        normalized_chunks=[nc.normalized for nc in normalized_list],
        total_substitutions=total,
        substitution_types=substitution_types,
    )


# ── Interface rapide (debug / tests) ──────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG)

    test_chunks = [
        "Le crédit d'impôt recherche (crédit impôt recherche) représente 30% des dépenses.",
        "Les activités de recherche et développement et de R-D couvrent 3 domaines.",
        "Le projet est au niveau de maturité technologique 4, visant le TRL-7.",
        "Budget : 150k€ de personnel R&D et 1.5M€ de sous-traitance.",
        "L'équipe comprend 3 équivalents-temps-plein (FTE) dédiés à la recherche.",
        "Les deliverables incluent 3 milestones et une roadmap sur 24 mois.",
        "L'état de l'art montre que le state of the art évolue rapidement.",
        "Les verrous techniques identifiés sont nombreux.",
        "Le projet est financé par Bpi France et l'agence nationale de la recherche.",
        # Doc emballage médical — RIEN ne doit être sur-normalisé ici
        "L'emballage doit résister aux chocs et à l'abrasion tout en restant recyclable.",
    ]

    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            test_chunks = [f.read()]

    result = normalize_chunks(test_chunks)

    print(f"\n{'═'*65}")
    print(f"Chunks normalisés : {len(result.normalized_chunks)}")
    print(f"Substitutions     : {result.total_substitutions}")
    print(f"Types             : {result.substitution_types}")
    print(f"{'─'*65}")

    for i, nc in enumerate(result.chunks):
        if nc.substitution_count == 0:
            print(f"\n[{i+1}] ({nc.language}) Aucune substitution")
            print(f"  {nc.original[:80]}")
            continue
        print(f"\n[{i+1}] ({nc.language}) {nc.substitution_count} substitution(s)")
        print(f"  AVANT : {nc.original[:100]}")
        print(f"  APRÈS : {nc.normalized[:100]}")
        for before, after in nc.substitutions:
            print(f"    ↳ {before} → {after}")

    print(f"\n{'═'*65}")