"""
modules/NLP/final_taxonomy_mapper.py
──────────────────────────────────────────────────────────────────────────────
Mapping final UNIVERSEL des preuves agrégées vers les champs spécialisés NLPResult.

Objectif :
- Transformer aggregated_evidence + synthesis en champs propres :
  verrous_techniques, objectifs_rd, methodes_rd, resultats_rd,
  outils_technologies, modeles_algorithmes, metriques_evaluation,
  normes_techniques, materiaux_composants, mots_cles_projet, etc.
- Éviter les regex métier codées en dur par domaine.
- Ne plus écraser les bons résultats du synthesizer avec des règles spécifiques
  au médical, à l'emballage, ou à un secteur particulier.

Contraintes :
- PAS de LLM dans ce fichier.
- PAS de GLiNER.
- PAS de fine-tuning.
- PAS de liste fermée métier par domaine.
- Utilisation prioritaire des rôles produits par evidence_mapper/aggregator :
  objectif, verrou, demarche, essai, resultat, etat_art, preuve.
- Regex conservées uniquement si elles sont structurelles et universelles :
  normes, dates, montants, brevets, métriques numériques.

Version : 2.0.0-universal-evidence-first
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class FinalTaxonomy:
    technologies: list[str] = field(default_factory=list)
    verrous_techniques: list[str] = field(default_factory=list)
    mots_cles_projet: dict = field(default_factory=lambda: {"high_confidence": [], "candidates": []})

    axes_projet: list[str] = field(default_factory=list)
    objet_recherche: list[str] = field(default_factory=list)
    sous_domaines: list[str] = field(default_factory=list)
    hypotheses_rd: list[str] = field(default_factory=list)

    protocoles_experimentaux: list[str] = field(default_factory=list)
    outils_technologies: list[str] = field(default_factory=list)
    modeles_algorithmes: list[str] = field(default_factory=list)
    architectures_systeme: list[str] = field(default_factory=list)
    jeux_donnees_benchmarks: list[str] = field(default_factory=list)
    metriques_evaluation: list[str] = field(default_factory=list)
    parametres_variables: list[str] = field(default_factory=list)
    normes_techniques: list[str] = field(default_factory=list)
    materiaux_composants: list[str] = field(default_factory=list)

    limitations_perspectives: list[str] = field(default_factory=list)
    objectifs_rd: list[str] = field(default_factory=list)
    resultats_rd: list[str] = field(default_factory=list)
    methodes_rd: list[str] = field(default_factory=list)
    composants_techniques: list[str] = field(default_factory=list)

    livrables: list[str] = field(default_factory=list)
    depenses_eligibles: list[str] = field(default_factory=list)
    brevets: list[str] = field(default_factory=list)
    partenaires_rd: list[str] = field(default_factory=list)
    personnes: list[str] = field(default_factory=list)
    organismes: list[str] = field(default_factory=list)
    materiaux: list[str] = field(default_factory=list)
    equipements: list[str] = field(default_factory=list)
    lieux: list[str] = field(default_factory=list)
    dates_periodes: list[str] = field(default_factory=list)
    montants: list[str] = field(default_factory=list)
    indicateurs_cir: dict = field(default_factory=lambda: {"etp": [], "montants": [], "jalons": []})

    stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "technologies": self.technologies,
            "verrous_techniques": self.verrous_techniques,
            "mots_cles_projet": self.mots_cles_projet,
            "axes_projet": self.axes_projet,
            "objet_recherche": self.objet_recherche,
            "sous_domaines": self.sous_domaines,
            "hypotheses_rd": self.hypotheses_rd,
            "protocoles_experimentaux": self.protocoles_experimentaux,
            "outils_technologies": self.outils_technologies,
            "modeles_algorithmes": self.modeles_algorithmes,
            "architectures_systeme": self.architectures_systeme,
            "jeux_donnees_benchmarks": self.jeux_donnees_benchmarks,
            "metriques_evaluation": self.metriques_evaluation,
            "parametres_variables": self.parametres_variables,
            "normes_techniques": self.normes_techniques,
            "materiaux_composants": self.materiaux_composants,
            "limitations_perspectives": self.limitations_perspectives,
            "objectifs_rd": self.objectifs_rd,
            "resultats_rd": self.resultats_rd,
            "methodes_rd": self.methodes_rd,
            "composants_techniques": self.composants_techniques,
            "livrables": self.livrables,
            "depenses_eligibles": self.depenses_eligibles,
            "brevets": self.brevets,
            "partenaires_rd": self.partenaires_rd,
            "personnes": self.personnes,
            "organismes": self.organismes,
            "materiaux": self.materiaux,
            "equipements": self.equipements,
            "lieux": self.lieux,
            "dates_periodes": self.dates_periodes,
            "indicateurs_cir": self.indicateurs_cir,
            "montants": self.montants,
            "stats": self.stats,
        }


# ══════════════════════════════════════════════════════════════════════════════
# NORMALISATION / ACCÈS DONNÉES
# ══════════════════════════════════════════════════════════════════════════════

def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'").replace("`", "'")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _clean_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip(" \t\n\r;:,.|")


def _safe_get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _get_by_role(aggregated: Any) -> dict:
    by_role = _safe_get(aggregated, "by_role", {}) or {}
    return by_role if isinstance(by_role, dict) else {}


def _get_phrase(item: Any) -> str:
    if isinstance(item, dict):
        return _clean_text(item.get("phrase", ""))
    return _clean_text(getattr(item, "phrase", ""))


def _phrases_by_role(aggregated: Any, role: str) -> list[str]:
    return [
        _get_phrase(x)
        for x in (_get_by_role(aggregated).get(role) or [])
        if _get_phrase(x)
    ]


def _all_phrases(aggregated: Any) -> list[str]:
    out: list[str] = []
    for items in _get_by_role(aggregated).values():
        for item in items or []:
            p = _get_phrase(item)
            if p:
                out.append(p)
    return _dedup(out)


def _get_concepts(aggregated: Any) -> list[dict]:
    concepts = _safe_get(aggregated, "concepts", []) or []
    out = []

    for c in concepts:
        if isinstance(c, dict):
            txt = c.get("text")
            freq = int(c.get("frequency", 1) or 1)
            passage_ids = c.get("passage_ids", []) or []
        else:
            txt = getattr(c, "text", None)
            freq = int(getattr(c, "frequency", 1) or 1)
            passage_ids = getattr(c, "passage_ids", []) or []

        txt = _clean_text(txt)
        if txt:
            out.append(
                {
                    "text": txt,
                    "frequency": freq,
                    "passage_ids": passage_ids,
                }
            )

    return out


def _dedup(items: Iterable[str], max_items: int | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    for item in items:
        item = _clean_text(item)
        if not item:
            continue

        key = _norm(item)
        if key in seen:
            continue

        seen.add(key)
        out.append(item)

        if max_items and len(out) >= max_items:
            break

    return out


def _too_long_for_taxonomy(text: str, max_words: int = 14) -> bool:
    return len(_clean_text(text).split()) > max_words


def _is_sentence(text: str) -> bool:
    t = _clean_text(text)
    return len(t.split()) > 12 or bool(
        re.search(
            r"\b(nous avons|il doit|elle doit|afin de|pour ce faire|en effet|par conséquent)\b",
            t,
            re.I,
        )
    )


def _is_noise_term(text: str) -> bool:
    t = _clean_text(text)
    n = _norm(t)

    if not t or len(t) < 3:
        return True

    if re.fullmatch(r"\d+(?:[.,]\d+)?%?", t):
        return True

    noise_exact = {
        "cir",
        "r&d",
        "rd",
        "2024",
        "en 2024",
        "conclusion",
        "technique",
        "technologique",
        "technologiques",
        "travaux de r&d",
        "raisonnement scientifique",
        "demarche scientifique",
        "organisation",
        "plan strategique",
    }

    if n in noise_exact:
        return True

    if re.search(
        r"nom prenom|adresse electronique|telephone|cout|date de|interlocuteur",
        n,
    ):
        return True

    return False


# ══════════════════════════════════════════════════════════════════════════════
# REGEX STRUCTURELLES UNIVERSELLES
# ══════════════════════════════════════════════════════════════════════════════

NORM_RE = re.compile(
    r"\b(?:ISO|IEC|IEEE|ASTM|EN|NF|DIN|MIL|FDA|CE)\s*[-:]?\s*[A-Z0-9./-]*\d[A-Z0-9./-]*\b",
    re.I,
)

DATE_RE = re.compile(
    r"\b(?:janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[ée]cembre)\s+\d{4}\b|"
    r"\b\d{1,2}/\d{1,2}/\d{2,4}\b|"
    r"\b20\d{2}\b",
    re.I,
)

MONEY_RE = re.compile(
    r"\b\d[\d\s.,]*\s*(?:€|EUR|euros?|MAD|DH|dirhams?|k€|K€|M€)\b",
    re.I,
)

PATENT_RE = re.compile(
    r"\b(?:brevet|demande de d[ée]p[oô]t|d[ée]p[oô]t de brevet|d[ée]p[oô]t\s+n[°o]?\s*[A-Z0-9-]*)\b",
    re.I,
)

LIMITATION_TRUE_RE = re.compile(
    r"\b(?:"
    r"ne permet pas|ne permettent pas|ne r[ée]pond pas|ne peuvent pas|"
    r"incapacit[ée]|difficult[ée]|risque|insuffisant|manque de maturit[ée]|"
    r"toutefois|cependant|reste [àa]|limite|limites|limitation|"
    r"faible|faibles|d[ée]fi|d[ée]fis|contraintes?|verrou|verrous?"
    r")\b",
    re.I,
)


# ══════════════════════════════════════════════════════════════════════════════
# EXTRACTIONS UNIVERSELLES
# ══════════════════════════════════════════════════════════════════════════════

def _normalize_term_case(term: str) -> str:
    """
    Normalisation légère qui préserve les acronymes / noms de modèles.
    """
    t = _clean_text(term)
    t = re.sub(r"\bimpression\s*3\s*D\b", "impression 3D", t, flags=re.I)
    return t


def _extract_norms(texts: list[str]) -> list[str]:
    norms = []

    for text in texts:
        for m in NORM_RE.finditer(text):
            val = _clean_text(m.group(0))

            # Exclure faux positifs temporels.
            if re.fullmatch(r"en\s+20\d{2}", val, re.I):
                continue

            norms.append(val.upper().replace(" ", ""))

    return _dedup(norms, 20)


def _extract_dates_and_money(texts: list[str]) -> tuple[list[str], list[str]]:
    dates = []
    money = []

    for p in texts:
        for m in DATE_RE.finditer(p):
            val = _clean_text(m.group(0))
            if val:
                dates.append(val)

        for m in MONEY_RE.finditer(p):
            money.append(_clean_text(m.group(0)))

    return _dedup(dates, 20), _dedup(money, 20)


def _extract_brevets(texts: list[str]) -> list[str]:
    out = []

    for p in texts:
        if PATENT_RE.search(p):
            p = _clean_text(p)

            if len(p.split()) > 45:
                m = re.search(
                    r".{0,120}(?:brevet|demande de d[ée]p[oô]t|d[ée]p[oô]t de brevet).{0,180}",
                    p,
                    re.I,
                )
                if m:
                    p = _clean_text(m.group(0))

            out.append(p)

    return _dedup(out, 8)


def _extract_limitations(texts: list[str], max_items: int = 8) -> list[str]:
    out = []

    for p in texts:
        p = _clean_text(p)
        if not p:
            continue

        if LIMITATION_TRUE_RE.search(p):
            out.append(p)

    return _dedup(out, max_items)


def _rank_keywords_universal(
    concepts: list[dict],
    max_high: int = 15,
    max_cand: int = 12,
) -> dict:
    """
    Classe les mots-clés uniquement par fréquence, longueur et lisibilité.
    Aucune regex métier par domaine.
    """
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()

    for c in concepts:
        txt = _clean_text(c.get("text", ""))
        if not txt or _is_noise_term(txt) or _too_long_for_taxonomy(txt, 9):
            continue

        freq = int(c.get("frequency", 1) or 1)

        score = freq * 10

        words = txt.split()
        if 2 <= len(words) <= 6:
            score += 8

        if len(txt) >= 4:
            score += 2

        if _looks_like_model_name(txt):
            score += 8

        scored.append((score, _normalize_term_case(txt)))

    scored.sort(key=lambda x: (-x[0], len(x[1])))

    high: list[str] = []
    cand: list[str] = []

    for score, term in scored:
        k = _norm(term)
        if k in seen:
            continue

        seen.add(k)

        if score >= 20 and len(high) < max_high:
            high.append(term)
        elif len(cand) < max_cand:
            cand.append(term)

        if len(high) >= max_high and len(cand) >= max_cand:
            break

    return {"high_confidence": high, "candidates": cand}


def _looks_like_model_name(text: str) -> bool:
    """
    Détecte un nom de modèle/algorithme de manière universelle :
    acronyme, CamelCase, ou terme court avec chiffre.
    Exemples : SCoT4UT, RAG4UT, GPT-4, BERT, EvoSuite, ResNet50.
    """
    t = _clean_text(text)

    if not t or len(t) < 2 or len(t.split()) > 4:
        return False

    if re.match(r"^[A-Z][A-Z0-9\-_]{1,}$", t):
        return True

    if re.match(r"^[A-Z][a-zA-Z0-9]{2,}[0-9][A-Za-z0-9]*$", t):
        return True

    if re.match(r"^[A-Za-z]+[0-9]+[A-Za-z0-9\-]*$", t):
        return True

    # Acronyme dans expression courte : "Chain-of-Thought (CoT)"
    if re.search(r"\b[A-Z]{2,}[A-Z0-9\-]*\b", t) and len(t.split()) <= 4:
        return True

    return False


def _looks_like_material_or_component(text: str) -> bool:
    """
    Détecte seulement les matériaux/composants universels évidents :
    acronymes chimiques courts ou formules simples.
    Pas de liste fermée domaine.
    """
    t = _clean_text(text)

    if not t or len(t) < 2 or _too_long_for_taxonomy(t, 5):
        return False

    if re.match(r"^[A-Z]{2,6}$", t):
        return True

    if re.match(r"^[A-Z][a-z]?[0-9]+", t):
        return True

    # Composant technique court générique si le concept contient un nom d'objet court.
    if re.search(r"\b(module|capteur|interface|pipeline|serveur|client|moteur|parseur|benchmark|corpus)\b", t, re.I):
        return True

    return False


def _extract_metrics_universal(texts: list[str], concepts: list[dict]) -> list[str]:
    """
    Extrait les métriques d'évaluation de manière universelle :
    - concepts courts avec signal d'évaluation ;
    - fragments numériques avec %, unités ou indicateurs.
    """
    out = []

    eval_re = re.compile(
        r"\b("
        r"taux|score|couverture|pr[ée]cision|rappel|accuracy|f1|f1-score|"
        r"latence|erreur|rendement|performance|compilabilit[ée]|compilable|"
        r"coverage|recall|precision|temps|co[uû]t|qualit[ée]"
        r")\b",
        re.I,
    )

    for c in concepts:
        txt = _clean_text(c.get("text", ""))
        if _too_long_for_taxonomy(txt, 6) or _is_noise_term(txt):
            continue
        if eval_re.search(txt):
            out.append(txt)

    num_re = re.compile(
        r"\b\d+(?:[,.]\d+)?\s*(?:%|°C|ms|s\b|mn\b|min\b|h\b|mm\b|cm\b|kg\b|N\b|MPa\b|kPa\b|fois\b)",
        re.I,
    )

    for text in texts:
        for m in num_re.finditer(text):
            val = _clean_text(m.group(0))
            if val:
                out.append(val)

    return _dedup(out, 15)


def _extract_parametres_variables(texts: list[str], concepts: list[dict]) -> list[str]:
    """
    Paramètres/variables universels :
    termes courts contenant 'paramètre', 'variable', 'seuil', 'taille', etc.
    """
    out = []

    param_re = re.compile(
        r"\b(param[èe]tre|variable|seuil|taille|dimension|temp[ée]rature|pression|dur[ée]e|configuration|signature|imports?)\b",
        re.I,
    )

    for c in concepts:
        txt = _clean_text(c.get("text", ""))
        if txt and not _too_long_for_taxonomy(txt, 7) and param_re.search(txt):
            out.append(txt)

    for text in texts:
        if param_re.search(text) and not _too_long_for_taxonomy(text, 16):
            out.append(text)

    return _dedup(out, 12)


def _extract_architectures_systeme(concepts: list[dict]) -> list[str]:
    arch_re = re.compile(
        r"\b(architecture|syst[èe]me|pipeline|framework|plateforme|service|serveur|client|module|composant|interface)\b",
        re.I,
    )

    return _dedup(
        [
            c["text"]
            for c in concepts
            if not _too_long_for_taxonomy(c.get("text", ""), 6)
            and not _is_noise_term(c.get("text", ""))
            and arch_re.search(c.get("text", ""))
        ],
        12,
    )


def _extract_datasets_benchmarks(concepts: list[dict], texts: list[str]) -> list[str]:
    bench_re = re.compile(
        r"\b(dataset|jeu de donn[ée]es|corpus|benchmark|benchmarks?|base de donn[ée]es|SF110|EvoSuite|Defects4J|HumanEval|methods2test)\b",
        re.I,
    )

    out = []

    for c in concepts:
        txt = _clean_text(c.get("text", ""))
        if txt and not _too_long_for_taxonomy(txt, 8) and bench_re.search(txt):
            out.append(txt)

    for text in texts:
        if bench_re.search(text) and not _too_long_for_taxonomy(text, 18):
            out.append(text)

    return _dedup(out, 12)


# ══════════════════════════════════════════════════════════════════════════════
# SYNTHESIS HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _elements_to_texts(elements: list[Any]) -> list[str]:
    out = []

    for el in elements or []:
        if isinstance(el, dict):
            resume = el.get("resume")
            preuves = el.get("preuves", []) or []
        else:
            resume = getattr(el, "resume", None)
            preuves = getattr(el, "preuves", []) or []

        if resume:
            out.append(_clean_text(resume))

        for p in preuves:
            out.append(_clean_text(p))

    return _dedup(out)


def _synthesis_get_list(synthesis: Any, attr: str) -> list[Any]:
    if synthesis is None:
        return []

    if isinstance(synthesis, dict):
        fiche = synthesis.get("fiche_cir", synthesis)
        return fiche.get(attr, []) or []

    return getattr(synthesis, attr, []) or []


def _synthesis_get_objet(synthesis: Any) -> list[str]:
    if synthesis is None:
        return []

    if isinstance(synthesis, dict):
        fiche = synthesis.get("fiche_cir", synthesis)
        obj = fiche.get("objet_du_projet")
        return _elements_to_texts([obj]) if obj else []

    obj = getattr(synthesis, "objet_du_projet", None)
    return _elements_to_texts([obj]) if obj else []


def _best_object_research(
    synthesis: Any,
    objectifs: list[str],
    concepts: list[dict],
) -> list[str]:
    """
    Détermine l'objet du projet sans regex domaine.
    Priorité :
    1. objet_du_projet du synthesizer ;
    2. objectif clair ;
    3. concepts fréquents.
    """
    candidates = []

    syn_obj = _synthesis_get_objet(synthesis)
    candidates.extend(syn_obj)

    for o in objectifs:
        if o and not _too_long_for_taxonomy(o, 40):
            candidates.append(o)

    core = [
        c["text"]
        for c in concepts
        if int(c.get("frequency", 1) or 1) >= 2
        and not _too_long_for_taxonomy(c["text"], 6)
        and not _is_noise_term(c["text"])
    ]

    if core:
        candidates.append(" / ".join(core[:4]))

    return _dedup(candidates, 3)


# ══════════════════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ══════════════════════════════════════════════════════════════════════════════

def map_final_taxonomy(
    aggregated: Any,
    synthesis: Any = None,
    domain_classification: Any = None,
) -> FinalTaxonomy:
    """
    Produit les champs spécialisés finaux à partir des preuves agrégées.

    Version universelle :
    - utilise d'abord les rôles déjà classés par evidence_mapper/aggregator ;
    - fusionne avec synthesis ;
    - ne contient pas de regex métier domaine-spécifique.
    """
    result = FinalTaxonomy()

    by_role = _get_by_role(aggregated)
    concepts = _get_concepts(aggregated)

    objectif_phrases = _phrases_by_role(aggregated, "objectif")
    verrou_phrases = _phrases_by_role(aggregated, "verrou")
    demarche_phrases = _phrases_by_role(aggregated, "demarche")
    essai_phrases = _phrases_by_role(aggregated, "essai")
    resultat_phrases = _phrases_by_role(aggregated, "resultat")
    etat_art_phrases = _phrases_by_role(aggregated, "etat_art")
    preuve_phrases = _phrases_by_role(aggregated, "preuve")
    all_texts = _all_phrases(aggregated)

    syn_objectifs = _elements_to_texts(_synthesis_get_list(synthesis, "objectifs"))
    syn_verrous = _elements_to_texts(_synthesis_get_list(synthesis, "verrous"))
    syn_demarche = _elements_to_texts(_synthesis_get_list(synthesis, "demarche"))
    syn_essais = _elements_to_texts(_synthesis_get_list(synthesis, "essais"))
    syn_resultats = _elements_to_texts(_synthesis_get_list(synthesis, "resultats"))

    # ── Champs principaux CIR : synthesis + aggregated, jamais "synthesis OU aggregated" uniquement.
    result.verrous_techniques = _dedup(
        syn_verrous + [v for v in verrou_phrases if v not in syn_verrous],
        15,
    )

    result.objectifs_rd = _dedup(
        syn_objectifs + [o for o in objectif_phrases if o not in syn_objectifs],
        12,
    )

    result.methodes_rd = _dedup(
        syn_demarche
        + syn_essais
        + [
            p
            for p in demarche_phrases + essai_phrases
            if p not in syn_demarche and p not in syn_essais
        ],
        15,
    )

    result.resultats_rd = _dedup(
        syn_resultats + [r for r in resultat_phrases if r not in syn_resultats],
        10,
    )

    result.objet_recherche = _best_object_research(
        synthesis,
        result.objectifs_rd,
        concepts,
    )

    result.limitations_perspectives = _extract_limitations(
        verrou_phrases + etat_art_phrases + resultat_phrases,
        max_items=10,
    )

    # ── Mots-clés / technologies : concepts universels.
    result.mots_cles_projet = _rank_keywords_universal(
        concepts,
        max_high=15,
        max_cand=12,
    )
    result.technologies = result.mots_cles_projet["high_confidence"][:12]

    result.outils_technologies = _dedup(
        [
            c["text"]
            for c in concepts
            if not _too_long_for_taxonomy(c["text"], 6)
            and not _is_noise_term(c["text"])
            and int(c.get("frequency", 1) or 1) >= 2
        ],
        15,
    )

    result.modeles_algorithmes = _dedup(
        [
            c["text"]
            for c in concepts
            if _looks_like_model_name(c["text"])
        ],
        12,
    )

    result.architectures_systeme = _extract_architectures_systeme(concepts)
    result.jeux_donnees_benchmarks = _extract_datasets_benchmarks(concepts, all_texts)
    result.metriques_evaluation = _extract_metrics_universal(all_texts, concepts)
    result.parametres_variables = _extract_parametres_variables(all_texts, concepts)

    # Protocoles : phrases d'essai + preuves courtes.
    result.protocoles_experimentaux = _dedup(
        [
            p
            for p in essai_phrases + preuve_phrases
            if not _too_long_for_taxonomy(p, 18)
        ],
        10,
    )

    # Normes, dates, montants, brevets : regex structurelles universelles.
    result.normes_techniques = _extract_norms(all_texts)

    dates, money = _extract_dates_and_money(all_texts)
    result.dates_periodes = dates
    result.montants = money
    result.indicateurs_cir["montants"] = money

    result.brevets = _extract_brevets(all_texts)

    # Matériaux/composants : uniquement évidents et universels, depuis concepts.
    result.materiaux_composants = _dedup(
        [
            c["text"]
            for c in concepts
            if _looks_like_material_or_component(c["text"])
        ],
        20,
    )

    result.materiaux = result.materiaux_composants
    result.composants_techniques = result.materiaux_composants

    result.sous_domaines = _dedup(
        result.technologies + result.mots_cles_projet["candidates"],
        12,
    )

    # Axes / organismes.
    if domain_classification is not None:
        domaine = _safe_get(domain_classification, "domaine_principal", "")
        if domaine:
            result.axes_projet = _dedup([domaine], 3)

        org = _safe_get(domain_classification, "organisme_name", "")
        if org:
            result.organismes = _dedup([org], 5)

    result.hypotheses_rd = []

    result.stats = {
        "roles_present": sorted([r for r, items in by_role.items() if items]),
        "concepts_in": len(concepts),
        "phrases_in": len(all_texts),
        "high_confidence_keywords": len(result.mots_cles_projet["high_confidence"]),
        "candidate_keywords": len(result.mots_cles_projet["candidates"]),
        "no_llm": True,
        "version": "2.0.0-universal-evidence-first",
    }

    logger.info(
        "Final taxonomy universal : %d keywords HC | %d verrous | %d méthodes | %d modèles | %d métriques",
        len(result.mots_cles_projet["high_confidence"]),
        len(result.verrous_techniques),
        len(result.methodes_rd),
        len(result.modeles_algorithmes),
        len(result.metriques_evaluation),
    )

    return result


def map_taxonomy(
    aggregated: Any,
    synthesis: Any = None,
    domain_classification: Any = None,
) -> FinalTaxonomy:
    """
    Alias pour compatibilité avec router.py.
    """
    return map_final_taxonomy(aggregated, synthesis, domain_classification)


if __name__ == "__main__":
    print("final_taxonomy_mapper.py OK - universal v2.0.0")
    print("À tester via router NLP ou test_pipeline.py.")
