# -*- coding: utf-8 -*-
from __future__ import annotations

"""
frascati_guard.py — V32 universal evidence-based cleanup

Objectif :
- garder le nettoyage strict de V31 ;
- ne PAS dépendre d'une liste exhaustive de domaines ;
- fonctionner même si domain.json ne contient pas le domaine du projet ;
- reconstruire des verrous implicites à partir de familles universelles :
  performance insuffisante, instabilité, thermique, usure/fiabilité,
  qualité de sortie, cause racine, compromis de contraintes, non-transférabilité.

Principe :
FrascatiGuard ne décide pas l'éligibilité CIR. Il produit :
- verrous_probables ;
- verrous_a_verifier ;
- faux_verrous_rejetes ;
avec validation humaine obligatoire.
"""

import hashlib
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

# -----------------------------------------------------------------------------
# Configuration générale
# -----------------------------------------------------------------------------

PACK_KEYS = [
    "objectifs_locaux",
    "verrous_rnd_locaux",
    "methodes_locales",
    "resultats_locaux",
    "limites_locales",
    "contributions_locales",
    "etat_art_local",
    "parametres_locaux",
]

CONTEXT_ONLY_TYPES = {
    "norme_reglementation",
    "plan_schema",
    "administratif",
    "template_formulaire",
    "preuve_depot",
}

BAD_AS_VERROU_CATEGORIES = {
    "methodes_locales",
    "parametres_locaux",
    "contributions_locales",
}

MAX_PROBABLE = 8
MAX_TO_VALIDATE_WITH_EXPLICIT = 5
MAX_TO_VALIDATE_RAW = 8
MAX_REJECTED = 30

# -----------------------------------------------------------------------------
# Patterns universels Frascati
# -----------------------------------------------------------------------------

UNCERTAINTY_PATTERNS = [
    r"\bincertitude\b",
    r"\bverrou\b",
    r"\bprobl[eè]me\b",
    r"\bprobl[eé]matique\b",
    r"\bnon[- ]?r[eé]solu\b",
    r"\bnon[- ]?ma[iî]tris[ée]?\b",
    r"\bma[iî]trise\b",
    r"\bdifficile\b",
    r"\bdifficult[ée]\b",
    r"\blimite(s)?\b",
    r"\binsuffisan[ct]e?s?\b",
    r"\binsuffisance\b",
    r"\binstabilit[ée]\b",
    r"\binstable\b",
    r"\bexcessif\b",
    r"\banormal(e|es|ement)?\b",
    r"\bd[ée]faillan",
    r"\b[ée]chec(s)?\b",
    r"\berreur(s)?\b",
    r"\bfuite(s)?\b",
    r"\busure\b",
    r"\bd[ée]gradation\b",
    r"\b[ée]chauffement\b",
    r"\bpoint(s)? chaud(s)?\b",
    r"\btrop\s+(important|fort|[ée]lev[ée])",
    r"\bne permet pas\b",
    r"\bn[e']?a pas permis\b",
    r"\bimpossible de\b",
    r"\b[àa] v[ée]rifier\b",
    r"\b[àa] d[ée]montrer\b",
    r"\bhypoth[eè]se\b",
    r"\bcompromis\b",
    r"\bcontraintes? contradictoires?\b",
    r"\bnon[- ]?transposable\b",
    r"\bne (sont|peuvent|peut) pas (transpos|appliqu)",
]

SYSTEMATIC_PATTERNS = [
    r"\bessai(s)?\b",
    r"\btest(s)?\b",
    r"\bprototype(s)?\b",
    r"\bexp[ée]riment",
    r"\bit[ée]ration(s)?\b",
    r"\bmesur",
    r"\brelev[ée]s?\b",
    r"\bsimulation(s)?\b",
    r"\bmod[èe]le(s)?\b",
    r"\bmod[ée]lisation\b",
    r"\bcalcul(s)?\b",
    r"\bprotocole\b",
    r"\bvalidation\b",
    r"\bcomparaison\b",
    r"\bcomparer\b",
    r"\bcaract[ée]ris",
    r"\banalyse\b",
    r"\b[ée]tude\b",
    r"\bphase\b",
]

EVIDENCE_PATTERNS = [
    r"\b\d+[\.,]?\d*\s?%\b",
    r"\b\d+[\.,]?\d*\s?(db|dba|hz|rpm|tr/min|bar|bars|m3/h|m³/h|l/min|°c|c|mm|cm|m|kg|g|h|min|s)\b",
    r"\b\d+[\.,]?\d*\s?(mg|ml|µm|μm|nm|kda|mol)\b",
    r"\bfigure\s*\d+\b",
    r"\btableau\s*\d+\b",
    r"\br[ée]sultat(s)?\b",
    r"\bdonn[ée]es?\s+brutes?\b",
    r"\bcourbe(s)?\b",
    r"\bspectre\b",
    r"\bfft\b",
    r"\btemp[ée]rature(s)?\b",
    r"\bpression(s)?\b",
    r"\bd[ée]bit(s)?\b",
    r"\btaux\b",
    r"\bcouverture\b",
    r"\bcompilation\b",
]

NON_ROUTINE_PATTERNS = [
    r"\bnouveau\b",
    r"\bnouvelle\b",
    r"\bin[ée]dit(e)?\b",
    r"\bnon standard\b",
    r"\bsp[ée]cifique\b",
    r"\bcontraintes?\b",
    r"\bperformance(s)? vis[ée]e?s?\b",
    r"\bobjectif(s)? de performance\b",
    r"\b[ée]tat de l.?art\b",
    r"\bsolution(s)? existante(s)?\b",
    r"\blitt[ée]rature\b",
    r"\bbrevet(s)?\b",
    r"\btranspos",
    r"\boptimis",
    r"\bred[ée]finition\b",
    r"\bit[ée]ration\b",
    r"\barchitecture\b",
    r"\bcompromis\b",
    r"\bsimultan[ée]ment\b",
]

# Signaux de problèmes techniques universels.
PROBLEM_PATTERNS = [
    r"\bvibration(s)?\b",
    r"\bvibratoire\b",
    r"\bacoustique\b",
    r"\bbruit\b",
    r"\bsonore\b",
    r"\bd[ée]s[ée]quilibr",
    r"\bpoulie\b",
    r"\bcontrepoids\b",
    r"\b[ée]quilibrage\b",
    r"\b[ée]chauffement\b",
    r"\bthermique\b",
    r"\brefroidissement\b",
    r"\br[ée]frig[ée]rant\b",
    r"\btemp[ée]rature\b",
    r"\bpoint(s)? chaud(s)?\b",
    r"\bpression\b",
    r"\bd[ée]bit\b",
    r"\beau\b",
    r"\bhumidit[ée]\b",
    r"\bair sec\b",
    r"\bcondensat(s)?\b",
    r"\bs[ée]parateur\b",
    r"\b[ée]clateur\b",
    r"\bsoufflage carter\b",
    r"\breniflard\b",
    r"\bhuile\b",
    r"\bsegment(s)?\b",
    r"\bsegmentation\b",
    r"\bchemise\b",
    r"\bpiston\b",
    r"\busure\b",
    r"\bfrottement\b",
    r"\b[ée]tanch[ée]it[ée]\b",
    r"\bcompilation\b",
    r"\bex[ée]cution\b",
    r"\bexactitude\b",
    r"\bcorrectness\b",
    r"\bcouverture\b",
    r"\bqualit[ée]\b",
    r"\brobustesse\b",
    r"\bfiabilit[ée]\b",
]

CONSTRAINT_PATTERNS = [
    r"\b\d+[\.,]?\d*\s?(bar|bars|m3/h|m³/h|rpm|tr/min|hz|db|dba|°c|kg|mm)\b",
    r"\bhaute(s)?\s+pression(s)?\b",
    r"\bhaute(s)?\s+vitesse(s)?\b",
    r"\bfort(e|es)?\s+d[ée]bit",
    r"\bencombrement\b",
    r"\bcompacit[ée]\b",
    r"\bsous[- ]marin\b",
    r"\bmilitaire\b",
    r"\bconforme\b",
    r"\bexigence(s)?\b",
    r"\bcontraintes?\b",
    r"\blimit[ée]e?s?\b",
    r"\btemps r[ée]el\b",
    r"\blangage fortement typ[ée]\b",
    r"\bcontexte sp[ée]cifique\b",
]

METHOD_OR_CONTEXT_PATTERNS = [
    r"\btable des mati[èe]res\b",
    r"\bsommaire\b",
    r"\bpage\s+\d+\s+sur\s+\d+\b",
    r"\bsiret\b",
    r"\brcs\b",
    r"\bdate et signature\b",
    r"\b[ée]quipement(s)? utilis[ée]s?\b",
    r"\bmat[ée]riel utilis[ée]\b",
    r"\bsonom[èe]tre\b",
    r"\bacc[ée]l[ée]rom[èe]tre\b",
    r"\bmicrophone\b",
    r"\banalyseur\b",
    r"\bdosage\b",
    r"\bp[- ]?value\b",
    r"\bwilcoxon\b",
    r"\bconditions exp[ée]rimentales\b",
]

# -----------------------------------------------------------------------------
# Familles universelles de verrous implicites
# -----------------------------------------------------------------------------

UNIVERSAL_VERROU_THEMES = [
    {
        "theme_id": "performance_insuffisante",
        "label": "Performance insuffisante sous contrainte",
        "patterns": [
            r"\bperformance", r"\binsuffisan", r"\bobjectif", r"\bexigence", r"\bconforme",
            r"\bd[ée]bit", r"\bpression", r"\bcouverture", r"\bexactitude", r"\brendement",
        ],
        "question": "Le système atteint-il les performances visées malgré les contraintes techniques du projet ?",
    },
    {
        "theme_id": "instabilite_comportement",
        "label": "Comportement instable ou non maîtrisé",
        "patterns": [
            r"\binstabil", r"\bvibration", r"\bvibratoire", r"\bacoustique", r"\bbruit",
            r"\bd[ée]s[ée]quilibr", r"\bpoulie", r"\bcontrepoids", r"\b[ée]quilibrage",
            r"\br[ée]sonance", r"\bmode(s)?\b", r"\bfr[ée]quence",
        ],
        "question": "Le comportement du système reste-t-il stable et maîtrisé dans les conditions de fonctionnement visées ?",
    },
    {
        "theme_id": "contrainte_thermique",
        "label": "Maîtrise thermique / refroidissement",
        "patterns": [
            r"\bthermique", r"\btemp[ée]rature", r"\b[ée]chauffement", r"\bpoint(s)? chaud(s)?",
            r"\brefroidissement", r"\br[ée]frig[ée]rant", r"\bd[ée]bit d.?eau", r"\beau",
            r"\bcalorie", r"\bchaleur", r"\b[ée]change(s)? thermique(s)?",
        ],
        "question": "La solution permet-elle de maîtriser les températures et les échanges thermiques dans les conditions réelles ?",
    },
    {
        "theme_id": "qualite_sortie_non_conforme",
        "label": "Qualité de sortie non conforme ou difficile à garantir",
        "patterns": [
            r"\bqualit[ée]", r"\bconforme", r"\bnon conforme", r"\bair sec", r"\bhumidit[ée]",
            r"\bcondensat", r"\beau", r"\bhuile", r"\bs[ée]parateur", r"\b[ée]clateur",
            r"\bsoufflage carter", r"\breniflard", r"\bpoint de ros[ée]e",
            r"\bcompilation", r"\bex[ée]cution", r"\bexactitude", r"\berreur",
        ],
        "question": "La sortie produite respecte-t-elle les exigences de qualité malgré les contraintes du système ?",
    },
    {
        "theme_id": "usure_fiabilite",
        "label": "Fiabilité, usure ou dégradation en fonctionnement",
        "patterns": [
            r"\busure", r"\bd[ée]gradation", r"\bd[ée]faill", r"\bfrottement", r"\b[ée]tanch[ée]it[ée]",
            r"\bsegment", r"\bsegmentation", r"\bchemise", r"\bpiston", r"\bmatage", r"\bodeur de br[ûu]l[ée]",
            r"\bfiabilit[ée]", r"\bendurance", r"\brupture", r"\br[ée]sistance",
        ],
        "question": "La solution reste-t-elle fiable dans le temps sans usure ou dégradation incompatible avec l'usage visé ?",
    },
    {
        "theme_id": "cause_racine_inconnue",
        "label": "Identification de la cause racine",
        "patterns": [
            r"\bcause", r"\borigine", r"\bprovenir", r"\ble probl[eè]me vient", r"\bsemble", r"\bsemblerait",
            r"\b[àa] rechercher", r"\b[àa] identifier", r"\binvestigation", r"\banalyser quel",
            r"\bpas en relation", r"\bne semble pas",
        ],
        "question": "La cause technique réelle du problème est-elle identifiée et validée par des essais ou analyses ?",
    },
    {
        "theme_id": "compromis_contraintes",
        "label": "Compromis entre contraintes contradictoires",
        "patterns": [
            r"\bcompromis", r"\bsimultan[ée]ment", r"\bcontraintes?", r"\bencombrement",
            r"\bcompacit[ée]", r"\bperformance", r"\bpression", r"\bd[ée]bit", r"\bbruit", r"\btemp[ée]rature",
            r"\bsans d[ée]grader", r"\btout en", r"\bcompatible",
        ],
        "question": "Le projet parvient-il à satisfaire simultanément des contraintes techniques en tension ?",
    },
    {
        "theme_id": "non_transposabilite_etat_art",
        "label": "Non-transférabilité des solutions existantes",
        "patterns": [
            r"\b[ée]tat de l.?art", r"\blitt[ée]rature", r"\bbrevet", r"\bsolution(s)? existante(s)?",
            r"\bnon transposable", r"\bne peuvent pas", r"\bne peut pas", r"\bpas applicable",
            r"\bdiff[ée]rent", r"\barchitecture", r"\bcontexte", r"\bsp[ée]cifique",
        ],
        "question": "Les solutions connues sont-elles insuffisantes ou non transférables au contexte technique du projet ?",
    },
    {
        "theme_id": "adaptation_contexte_specifique",
        "label": "Adaptation à un contexte technique spécifique",
        "patterns": [
            r"\bsp[ée]cifique", r"\bcontexte", r"\badapt", r"\bjava", r"\bjunit", r"\bmockito",
            r"\bllm", r"\bprompt", r"\bbarillet", r"\bmulti[- ]?[ée]tage", r"\bsous[- ]marin",
            r"\bconditions r[ée]elles", r"\bconfiguration", r"\bvertical", r"\bhorizontal",
        ],
        "question": "La solution est-elle réellement adaptée au contexte technique spécifique du projet au-delà d'une application générique ?",
    },
]

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _norm(text: Any) -> str:
    text = str(text or "").lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’", "aaaeeeeiioouuuc'")
    text = text.translate(tr)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x if x is not None else default)
    except Exception:
        return default


def _hash(text: str) -> str:
    return hashlib.sha1(_clean(text).lower().encode("utf-8", errors="ignore")).hexdigest()[:12]


def _matches(patterns: List[str], text: Any) -> int:
    low = _norm(text)
    return sum(1 for p in patterns if re.search(p, low, flags=re.I))


def _has(patterns: List[str], text: Any) -> bool:
    return _matches(patterns, text) > 0


def _short(text: Any, max_chars: int = 1200) -> str:
    text = _clean(text)
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    pos = max(cut.rfind("."), cut.rfind(";"), cut.rfind("\n"))
    if pos < 350:
        pos = max_chars
    return cut[:pos].strip() + "…"


def _pack_item_iter(pack: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for key in PACK_KEYS:
        for item in pack.get(key, []) or []:
            if not isinstance(item, dict):
                continue
            x = dict(item)
            x.setdefault("_source_category", key)
            items.append(x)
    return items


def _dedupe_items(items: List[Dict[str, Any]], max_items: int = 999) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in items or []:
        txt = _clean(item.get("text") or item.get("source_text") or "")
        if not txt:
            continue
        key = (_norm(item.get("document", "")), _hash(txt[:900]))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= max_items:
            break
    return out


def _extract_domain_family(domain: Optional[Dict[str, Any]]) -> str:
    """
    Le domaine est facultatif. S'il n'est pas détecté, on retourne 'unknown'.
    La logique universelle fonctionne quand même.
    """
    domain = domain or {}
    blob = " ".join(str(domain.get(k, "")) for k in [
        "domain_code_niv1", "domain_label_niv1",
        "domain_code_niv2", "domain_label_niv2",
        "domain_code_niv3", "domain_label_niv3",
        "main_domain_code", "main_domain_label",
        "sub_domain_code", "sub_domain_label",
        "display_label",
    ])
    low = _norm(blob)
    if any(w in low for w in ["informatique", "logiciel", "programmation", "intelligence artificielle", "systemes d'information", "a3"]):
        return "informatique"
    if any(w in low for w in ["mecanique", "genie mecanique", "acoustique", "thermique", "energetique", "genie civil", "b4", "b7"]):
        return "mecanique"
    if any(w in low for w in ["biologie", "sante", "medical", "pharma", "chimie", "biochimie"]):
        return "bio_chimie"
    if low.strip():
        return "generic"
    return "unknown"


def _is_explicit_verrou(item: Dict[str, Any]) -> bool:
    role = str(item.get("role") or "")
    section_type = str(item.get("section_type") or "")
    section_title = _norm(item.get("section_title") or "")
    source_type = str(item.get("source_type") or "")
    content_origin = str(item.get("content_origin") or "")
    text = _norm(item.get("text") or "")

    if role == "verrou" and (source_type == "cir_structured" or content_origin == "cir_structured"):
        return True
    if section_type == "verrous":
        return True
    if "verrou" in section_title or "incertitude" in section_title:
        return True
    if re.search(r"\bverrou\s*\d+\b", text):
        return True
    return False


def _is_too_noisy(text: str) -> bool:
    low = _norm(text)
    if len(text) < 50:
        return True
    if low.count("page ") >= 2 and len(text) < 500:
        return True
    if len(re.findall(r"\b(slide|page|feuille)\b", low)) >= 6:
        return True
    if len(re.findall(r"\b\d{5,}\b", low)) >= 8:
        return True
    return False

# -----------------------------------------------------------------------------
# Guard principal
# -----------------------------------------------------------------------------

class FrascatiGuard:
    def __init__(self, mode: str = "raw_construction", domain: Optional[Dict[str, Any]] = None):
        self.mode = mode if mode in {"raw_construction", "cir_audit", "mixed"} else "raw_construction"
        self.domain = domain or {}
        self.domain_family = _extract_domain_family(self.domain)

    # ------------------------------------------------------------------
    # Analyse passage par passage
    # ------------------------------------------------------------------
    def analyze_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        text = _clean(item.get("text") or item.get("source_text") or "")
        role = str(item.get("role") or "unknown")
        source_category = str(item.get("_source_category") or item.get("pack_key") or "")
        dtype = str(item.get("document_type") or "")
        source_type = str(item.get("source_type") or "")
        content_origin = str(item.get("content_origin") or "")
        confidence = _safe_float(item.get("confidence") or item.get("model_confidence"), 0.0)
        verrou_score = _safe_float(item.get("verrou_score"), 0.0)

        uncertainty_hits = _matches(UNCERTAINTY_PATTERNS, text)
        systematic_hits = _matches(SYSTEMATIC_PATTERNS, text)
        evidence_hits = _matches(EVIDENCE_PATTERNS, text)
        nonroutine_hits = _matches(NON_ROUTINE_PATTERNS, text)
        problem_hits = _matches(PROBLEM_PATTERNS, text)
        constraint_hits = _matches(CONSTRAINT_PATTERNS, text)
        method_context_hits = _matches(METHOD_OR_CONTEXT_PATTERNS, text)

        is_context_doc = dtype in CONTEXT_ONLY_TYPES
        is_cir_structured = source_type == "cir_structured" or content_origin == "cir_structured"
        explicit_verrou = _is_explicit_verrou(item)
        too_noisy = _is_too_noisy(text)

        novelty = min(1.0, 0.18 * nonroutine_hits + (0.20 if "etat_art" in source_category else 0.0))
        uncertainty = min(
            1.0,
            0.24 * uncertainty_hits
            + 0.18 * problem_hits
            + 0.20 * verrou_score
            + (0.20 if role in {"verrou", "limite"} else 0.0),
        )
        systematic = min(1.0, 0.16 * systematic_hits + (0.18 if role in {"methode", "resultat"} else 0.0))
        evidence = min(1.0, 0.16 * evidence_hits + 0.10 * constraint_hits + (0.15 if role == "resultat" else 0.0))
        non_routine = min(1.0, 0.18 * nonroutine_hits + 0.10 * constraint_hits + 0.08 * problem_hits)

        # Bonus CIR structuré ou section verrou explicite.
        if explicit_verrou:
            uncertainty = max(uncertainty, 0.55)
            novelty = max(novelty, 0.35)
            non_routine = max(non_routine, 0.35)

        # Score indicatif.
        frascati_score = (
            0.22 * novelty
            + 0.32 * uncertainty
            + 0.17 * systematic
            + 0.14 * evidence
            + 0.15 * non_routine
        )

        false_reasons: List[str] = []

        # Documents contextuels : ne pas transformer un plan/norme en verrou.
        if is_context_doc and not explicit_verrou:
            frascati_score *= 0.35
            false_reasons.append("document contextuel/normatif/plan : utile comme preuve mais insuffisant comme verrou")

        # Méthodes/paramètres/contributions : rejet sauf si vraie incertitude + problème + contrainte.
        weak_category = source_category in BAD_AS_VERROU_CATEGORIES
        strong_problem_faisceau = (problem_hits >= 1 and (uncertainty_hits >= 1 or constraint_hits >= 1) and (systematic_hits >= 1 or evidence_hits >= 1))
        if weak_category and not explicit_verrou and not strong_problem_faisceau:
            frascati_score *= 0.25
            false_reasons.append("méthode, paramètre ou contribution : utile comme preuve mais pas comme verrou principal")

        # Passage très méthodologique ou administratif.
        if method_context_hits >= 2 and uncertainty_hits == 0 and problem_hits == 0:
            frascati_score *= 0.25
            false_reasons.append("passage surtout méthodologique/protocolaire sans incertitude technique")

        if too_noisy and not explicit_verrou:
            frascati_score *= 0.45
            false_reasons.append("passage trop court, concaténé ou bruité")

        # Si c'est un passage direct modèle verrou mais sans preuves Frascati, on ne le supprime pas forcément :
        # on le met à vérifier si c'est un problème technique clair.
        direct_verrou_candidate = (
            role == "verrou"
            or source_category == "verrous_rnd_locaux"
            or verrou_score >= 0.65
            or str(item.get("quality_status") or "") in {"verrou_boosted", "promoted_local_strict"}
        )

        if direct_verrou_candidate and problem_hits >= 1 and uncertainty >= 0.35:
            frascati_score = max(frascati_score, 0.42)

        frascati_score = max(0.0, min(1.0, frascati_score))

        criteria = {
            "novelty": round(novelty, 3),
            "uncertainty": round(uncertainty, 3),
            "systematic": round(systematic, 3),
            "evidence": round(evidence, 3),
            "non_routine": round(non_routine, 3),
            "nouveaute_non_routine": round(novelty, 3),
            "incertitude_scientifique_technique": round(uncertainty, 3),
            "demarche_systematique": round(systematic, 3),
            "preuves_resultats_tracabilite": round(evidence, 3),
            "depassement_simple_adaptation": round(non_routine, 3),
        }

        # Décision non bloquante.
        if explicit_verrou and frascati_score >= 0.45:
            decision = "verrou_probable"
        elif frascati_score >= 0.62 and uncertainty >= 0.45 and (systematic >= 0.20 or evidence >= 0.20 or explicit_verrou):
            decision = "verrou_probable"
        elif frascati_score >= 0.38 and (uncertainty >= 0.30 or problem_hits >= 1 or direct_verrou_candidate):
            decision = "verrou_a_verifier"
        elif direct_verrou_candidate and problem_hits >= 1 and not is_context_doc:
            decision = "verrou_a_verifier"
        elif direct_verrou_candidate or weak_category:
            decision = "faux_verrou_probable"
        else:
            decision = "indice_non_verrou"

        # En CIR structuré, une section verrou ne doit jamais être rejetée automatiquement.
        if is_cir_structured and explicit_verrou and decision == "faux_verrou_probable":
            decision = "verrou_a_verifier"
            false_reasons.append("section CIR verrou : à auditer plutôt qu'à rejeter automatiquement")

        reasons = self._build_reasons(
            decision=decision,
            uncertainty_hits=uncertainty_hits,
            systematic_hits=systematic_hits,
            evidence_hits=evidence_hits,
            nonroutine_hits=nonroutine_hits,
            problem_hits=problem_hits,
            constraint_hits=constraint_hits,
            false_reasons=false_reasons,
        )

        return {
            "decision": decision,
            "frascati_score": round(frascati_score, 4),
            "criteria": criteria,
            "signals": {
                "uncertainty_hits": uncertainty_hits,
                "systematic_hits": systematic_hits,
                "evidence_hits": evidence_hits,
                "nonroutine_hits": nonroutine_hits,
                "problem_hits": problem_hits,
                "constraint_hits": constraint_hits,
                "method_context_hits": method_context_hits,
            },
            "reasons": reasons,
            "needs_human_validation": decision in {"verrou_probable", "verrou_a_verifier", "faux_verrou_probable"},
            "original_role": role,
            "original_confidence": confidence,
            "original_verrou_score": verrou_score,
            "source_category": source_category,
            "explicit_verrou": explicit_verrou,
        }

    def _build_reasons(
        self,
        decision: str,
        uncertainty_hits: int,
        systematic_hits: int,
        evidence_hits: int,
        nonroutine_hits: int,
        problem_hits: int,
        constraint_hits: int,
        false_reasons: List[str],
    ) -> List[str]:
        reasons: List[str] = []
        if decision == "verrou_probable":
            reasons.append("verrou R&D probable selon la grille Frascati")
        elif decision == "verrou_a_verifier":
            reasons.append("signal R&D partiel : validation consultant nécessaire")
        elif decision == "faux_verrou_probable":
            reasons.append("passage utile mais insuffisant comme verrou R&D")
        else:
            reasons.append("indice conservé comme contexte, méthode, résultat ou contrainte")

        if problem_hits:
            reasons.append("problème technique détecté")
        if uncertainty_hits:
            reasons.append("incertitude, limite ou difficulté détectée")
        if constraint_hits:
            reasons.append("contrainte de performance ou de contexte détectée")
        if systematic_hits:
            reasons.append("démarche, mesure, test ou protocole détecté")
        if evidence_hits:
            reasons.append("preuve, métrique ou résultat détecté")
        if nonroutine_hits:
            reasons.append("non-routine, état de l'art, comparaison ou redéfinition détecté")
        reasons.extend(false_reasons)
        return reasons

    # ------------------------------------------------------------------
    # Qualification globale
    # ------------------------------------------------------------------
    def qualify(self, pack: Dict[str, Any], documents: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        pack = pack or {}
        all_items = _pack_item_iter(pack)

        verrous_probables: List[Dict[str, Any]] = []
        verrous_a_verifier: List[Dict[str, Any]] = []
        faux_verrous_rejetes: List[Dict[str, Any]] = []
        indices_non_verrous: List[Dict[str, Any]] = []

        explicit_count = 0

        for item in all_items:
            analysis = self.analyze_item(item)
            x = dict(item)
            x["frascati"] = analysis
            x["needs_human_validation"] = analysis["needs_human_validation"]
            if analysis.get("explicit_verrou"):
                explicit_count += 1

            if analysis["decision"] == "verrou_probable":
                x["role"] = "verrou"
                x["final_role"] = "verrou_probable"
                x["quality_status"] = "frascati_probable"
                x["rank_score"] = max(_safe_float(x.get("rank_score")), analysis["frascati_score"] + 0.45)
                verrous_probables.append(x)
            elif analysis["decision"] == "verrou_a_verifier":
                x["role"] = "verrou"
                x["final_role"] = "verrou_a_verifier"
                x["quality_status"] = "frascati_to_validate"
                x["rank_score"] = max(_safe_float(x.get("rank_score")), analysis["frascati_score"] + 0.25)
                verrous_a_verifier.append(x)
            elif analysis["decision"] == "faux_verrou_probable":
                x["rejected_as_verrou"] = True
                x["final_role"] = self._fallback_role_for_rejected(x)
                faux_verrous_rejetes.append(x)
            else:
                indices_non_verrous.append(x)

        # Reconstruction universelle : surtout utile quand il n'y a pas de verrou explicite.
        # Elle fonctionne même si domain_family = unknown/generic.
        implicit_items = self._build_universal_implicit_verrous(
            pack=pack,
            existing=verrous_probables + verrous_a_verifier,
            explicit_count=explicit_count,
        )
        verrous_a_verifier.extend(implicit_items)

        verrous_probables = self._sort_and_dedupe(verrous_probables, max_items=MAX_PROBABLE)

        if explicit_count > 0:
            verrous_a_verifier = self._filter_to_validate_when_explicit_exists(verrous_a_verifier)
            max_to_validate = MAX_TO_VALIDATE_WITH_EXPLICIT
        else:
            max_to_validate = MAX_TO_VALIDATE_RAW

        verrous_a_verifier = self._sort_and_dedupe(verrous_a_verifier, max_items=max_to_validate)
        faux_verrous_rejetes = self._sort_and_dedupe(faux_verrous_rejetes, max_items=MAX_REJECTED)

        qualified_pack = self._build_qualified_pack(
            original_pack=pack,
            verrous_probables=verrous_probables,
            verrous_a_verifier=verrous_a_verifier,
            faux_verrous_rejetes=faux_verrous_rejetes,
        )
        risk_report = self._risk_report(verrous_probables, verrous_a_verifier, faux_verrous_rejetes, documents)
        consultant_view = self._consultant_view(verrous_probables, verrous_a_verifier, faux_verrous_rejetes, risk_report)

        return {
            "version": "frascati_guard_v32_universal_evidence_based",
            "mode": self.mode,
            "domain_family": self.domain_family,
            "principle": "Frascati est utilisé comme grille de qualification et de risque, pas comme décision automatique d'éligibilité.",
            "criteria_used": [
                "nouveauté / non-routine",
                "incertitude scientifique ou technique",
                "démarche systématique",
                "preuves / résultats / traçabilité",
                "dépassement d'une simple adaptation métier",
                "familles universelles de verrous quand le domaine est inconnu",
            ],
            "explicit_verrous_detected_count": explicit_count,
            "verrous_probables": verrous_probables,
            "verrous_a_verifier": verrous_a_verifier,
            "faux_verrous_rejetes": faux_verrous_rejetes,
            "questions_consultant": self._questions_consultant(verrous_probables, verrous_a_verifier, faux_verrous_rejetes),
            "risk_report": risk_report,
            "consultant_view": consultant_view,
            "display_summary": consultant_view,
            "qualified_pack_for_ennodiagnostic": qualified_pack,
        }

    # ------------------------------------------------------------------
    # Reconstruction universelle
    # ------------------------------------------------------------------
    def _build_universal_implicit_verrous(
        self,
        pack: Dict[str, Any],
        existing: List[Dict[str, Any]],
        explicit_count: int,
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        candidates.extend(self._build_implicit_from_single_passages(pack, existing=existing, explicit_count=explicit_count))
        candidates.extend(self._build_implicit_from_universal_themes(pack, existing=existing + candidates, explicit_count=explicit_count))
        return candidates

    def _build_implicit_from_single_passages(
        self,
        pack: Dict[str, Any],
        existing: List[Dict[str, Any]],
        explicit_count: int,
    ) -> List[Dict[str, Any]]:
        # Quand il existe déjà des verrous explicites, ne pas ajouter trop de passages isolés.
        if explicit_count > 0:
            return []

        source_keys = ["verrous_rnd_locaux", "limites_locales", "objectifs_locaux", "resultats_locaux"]
        existing_norm = _norm("\n".join(_clean(x.get("text") or "")[:500] for x in existing))
        out: List[Dict[str, Any]] = []

        for key in source_keys:
            for item in pack.get(key, []) or []:
                if not isinstance(item, dict):
                    continue
                text = _clean(item.get("text") or "")
                if len(text) < 80 or _is_too_noisy(text):
                    continue
                if _norm(text[:260]) in existing_norm:
                    continue

                uncertainty_hits = _matches(UNCERTAINTY_PATTERNS, text)
                systematic_hits = _matches(SYSTEMATIC_PATTERNS, text)
                evidence_hits = _matches(EVIDENCE_PATTERNS, text)
                nonroutine_hits = _matches(NON_ROUTINE_PATTERNS, text)
                problem_hits = _matches(PROBLEM_PATTERNS, text)
                constraint_hits = _matches(CONSTRAINT_PATTERNS, text)

                # Faisceau universel minimal.
                has_faisceau = (
                    problem_hits >= 1
                    and (uncertainty_hits >= 1 or constraint_hits >= 1 or _safe_float(item.get("verrou_score")) >= 0.55)
                    and (systematic_hits >= 1 or evidence_hits >= 1 or key == "verrous_rnd_locaux")
                )
                if not has_faisceau:
                    continue

                score = 0.40
                score += min(0.12, 0.03 * problem_hits)
                score += min(0.12, 0.03 * uncertainty_hits)
                score += min(0.10, 0.025 * constraint_hits)
                score += min(0.08, 0.020 * evidence_hits)
                score += min(0.08, 0.020 * systematic_hits)
                score = min(0.62, score)

                x = dict(item)
                x.setdefault("_source_category", key)
                x["role"] = "verrou"
                x["final_role"] = "verrou_implicite_a_verifier"
                x["quality_status"] = "frascati_universal_single_to_validate"
                x["needs_human_validation"] = True
                x["verrou_source"] = "universal_single_passage"
                x["frascati"] = {
                    "decision": "verrou_a_verifier",
                    "frascati_score": round(score, 4),
                    "criteria": {
                        "novelty": round(min(1.0, 0.15 * nonroutine_hits), 3),
                        "uncertainty": round(min(1.0, 0.25 + 0.12 * uncertainty_hits + 0.10 * problem_hits), 3),
                        "systematic": round(min(1.0, 0.15 * systematic_hits), 3),
                        "evidence": round(min(1.0, 0.15 * evidence_hits + 0.08 * constraint_hits), 3),
                        "non_routine": round(min(1.0, 0.12 * nonroutine_hits + 0.08 * constraint_hits), 3),
                    },
                    "signals": {
                        "uncertainty_hits": uncertainty_hits,
                        "systematic_hits": systematic_hits,
                        "evidence_hits": evidence_hits,
                        "nonroutine_hits": nonroutine_hits,
                        "problem_hits": problem_hits,
                        "constraint_hits": constraint_hits,
                    },
                    "reasons": [
                        "verrou implicite reconstruit depuis un passage problématique",
                        "le passage contient un problème technique et des contraintes ou preuves",
                        "à reformuler et valider avec le consultant",
                    ],
                    "needs_human_validation": True,
                }
                x["rank_score"] = max(_safe_float(x.get("rank_score")), score + 0.25)
                out.append(x)

        return out

    def _build_implicit_from_universal_themes(
        self,
        pack: Dict[str, Any],
        existing: List[Dict[str, Any]],
        explicit_count: int,
    ) -> List[Dict[str, Any]]:
        source_keys = [
            "verrous_rnd_locaux",
            "objectifs_locaux",
            "limites_locales",
            "methodes_locales",
            "resultats_locaux",
            "contributions_locales",
            "etat_art_local",
            "parametres_locaux",
        ]

        all_items: List[Dict[str, Any]] = []
        for key in source_keys:
            for item in pack.get(key, []) or []:
                if not isinstance(item, dict):
                    continue
                txt = _clean(item.get("text") or "")
                if len(txt) < 60 or _is_too_noisy(txt):
                    continue
                dtype = str(item.get("document_type") or "")
                # Les documents purement contextuels ne peuvent soutenir un thème que comme preuve secondaire.
                x = dict(item)
                x.setdefault("_source_category", key)
                x["_is_context_only"] = dtype in CONTEXT_ONLY_TYPES
                all_items.append(x)

        existing_norm = _norm("\n".join(_clean(x.get("text") or "")[:600] for x in existing))
        out: List[Dict[str, Any]] = []

        for theme in UNIVERSAL_VERROU_THEMES:
            patterns = theme.get("patterns") or []
            matched: List[Dict[str, Any]] = []
            for item in all_items:
                text = _clean(item.get("text") or "")
                if not text:
                    continue
                if _has(patterns, text):
                    matched.append(item)

            matched = self._select_theme_evidence(matched, max_items=6)
            if len(matched) < 2:
                continue

            joined = "\n".join(_clean(m.get("text") or "") for m in matched)
            uncertainty_hits = _matches(UNCERTAINTY_PATTERNS, joined)
            systematic_hits = _matches(SYSTEMATIC_PATTERNS, joined)
            evidence_hits = _matches(EVIDENCE_PATTERNS, joined)
            nonroutine_hits = _matches(NON_ROUTINE_PATTERNS, joined)
            problem_hits = _matches(PROBLEM_PATTERNS, joined)
            constraint_hits = _matches(CONSTRAINT_PATTERNS, joined)
            method_context_hits = _matches(METHOD_OR_CONTEXT_PATTERNS, joined)

            docs = sorted({str(m.get("document") or "") for m in matched if m.get("document")})
            role_sources = sorted({str(m.get("_source_category") or "") for m in matched if m.get("_source_category")})
            non_context_docs = sorted({str(m.get("document") or "") for m in matched if not m.get("_is_context_only") and m.get("document")})

            families = sum([
                1 if problem_hits > 0 else 0,
                1 if uncertainty_hits > 0 else 0,
                1 if systematic_hits > 0 else 0,
                1 if evidence_hits > 0 else 0,
                1 if constraint_hits > 0 else 0,
                1 if nonroutine_hits > 0 else 0,
            ])

            if families < 3:
                continue
            if problem_hits == 0:
                continue
            if len(non_context_docs) == 0:
                continue
            if method_context_hits >= 5 and uncertainty_hits == 0 and problem_hits <= 1:
                continue

            # Si un verrou explicite existe déjà, on garde seulement des thèmes complémentaires très forts.
            if explicit_count > 0 and not (problem_hits >= 3 and (constraint_hits >= 1 or evidence_hits >= 2)):
                continue

            score = 0.42
            score += min(0.12, 0.025 * problem_hits)
            score += min(0.10, 0.025 * uncertainty_hits)
            score += min(0.10, 0.020 * systematic_hits)
            score += min(0.10, 0.020 * evidence_hits)
            score += min(0.10, 0.020 * constraint_hits)
            score += min(0.08, 0.020 * nonroutine_hits)
            if len(non_context_docs) >= 2:
                score += 0.04
            score = max(0.0, min(0.68, score))

            label = str(theme.get("label") or theme.get("theme_id") or "Verrou implicite")
            question = str(theme.get("question") or "Quel est le verrou technique exact à valider ?")

            support_texts = []
            supporting_passages = []
            for m in matched:
                mt = _short(m.get("text") or "", max_chars=520)
                support_texts.append(f"- {mt}")
                supporting_passages.append({
                    "text": mt,
                    "document": m.get("document"),
                    "source_path": m.get("source_path"),
                    "content_origin": m.get("content_origin"),
                    "document_type": m.get("document_type"),
                    "section_title": m.get("section_title"),
                    "passage_id": m.get("passage_id"),
                    "confidence": m.get("confidence"),
                    "verrou_score": m.get("verrou_score"),
                    "quality_status": m.get("quality_status"),
                    "source_category": m.get("_source_category"),
                })

            synthetic_text = (
                f"Verrou implicite possible — {label}. "
                f"Question de qualification : {question} "
                f"Ce verrou est reconstruit à partir d'indices dispersés dans les documents bruts et doit être validé par le consultant. "
                f"Documents concernés : {', '.join(docs[:5]) if docs else 'à vérifier'}. "
                f"Indices sources : " + " ".join(support_texts[:4])
            )

            if _norm(synthetic_text[:320]) in existing_norm:
                continue

            base_item = matched[0]
            item = {
                "passage_id": f"implicit_universal_{theme.get('theme_id')}_{_hash(joined[:1500])}",
                "document": ", ".join(docs[:3]),
                "source_path": base_item.get("source_path"),
                "source_type": base_item.get("source_type", "raw"),
                "content_origin": base_item.get("content_origin"),
                "document_type": "multi_document_theme" if len(docs) > 1 else base_item.get("document_type"),
                "role": "verrou",
                "final_role": "verrou_implicite_a_verifier",
                "quality_status": "frascati_universal_theme_to_validate",
                "text": _short(synthetic_text, max_chars=1700),
                "theme_id": theme.get("theme_id"),
                "theme_label": label,
                "theme_question": question,
                "source_categories": role_sources,
                "supporting_passages": supporting_passages,
                "needs_human_validation": True,
                "verrou_source": "universal_theme_reconstruction",
                "frascati": {
                    "decision": "verrou_a_verifier",
                    "frascati_score": round(score, 4),
                    "criteria": {
                        "novelty": round(min(1.0, 0.15 * nonroutine_hits), 3),
                        "uncertainty": round(min(1.0, 0.25 + 0.10 * uncertainty_hits + 0.08 * problem_hits), 3),
                        "systematic": round(min(1.0, 0.13 * systematic_hits), 3),
                        "evidence": round(min(1.0, 0.13 * evidence_hits + 0.08 * constraint_hits), 3),
                        "non_routine": round(min(1.0, 0.13 * nonroutine_hits + 0.08 * constraint_hits), 3),
                    },
                    "signals": {
                        "uncertainty_hits": uncertainty_hits,
                        "systematic_hits": systematic_hits,
                        "evidence_hits": evidence_hits,
                        "nonroutine_hits": nonroutine_hits,
                        "problem_hits": problem_hits,
                        "constraint_hits": constraint_hits,
                        "method_context_hits": method_context_hits,
                        "documents_count": len(docs),
                        "non_context_documents_count": len(non_context_docs),
                        "source_categories": role_sources,
                    },
                    "reasons": [
                        "verrou implicite reconstruit par famille universelle de problème",
                        "indices croisés dans plusieurs passages ou catégories",
                        "à reformuler et valider avec le consultant",
                    ],
                    "needs_human_validation": True,
                },
                "rank_score": score + 0.35,
            }
            out.append(item)

        return out

    def _select_theme_evidence(self, items: List[Dict[str, Any]], max_items: int = 6) -> List[Dict[str, Any]]:
        def item_score(x: Dict[str, Any]) -> float:
            txt = _clean(x.get("text") or "")
            src = str(x.get("_source_category") or "")
            dtype = str(x.get("document_type") or "")
            score = 0.0
            score += 0.25 * _matches(PROBLEM_PATTERNS, txt)
            score += 0.20 * _matches(UNCERTAINTY_PATTERNS, txt)
            score += 0.15 * _matches(CONSTRAINT_PATTERNS, txt)
            score += 0.12 * _matches(SYSTEMATIC_PATTERNS, txt)
            score += 0.10 * _matches(EVIDENCE_PATTERNS, txt)
            score += 0.10 * _matches(NON_ROUTINE_PATTERNS, txt)
            if src in {"verrous_rnd_locaux", "limites_locales", "objectifs_locaux", "resultats_locaux"}:
                score += 0.35
            if dtype in CONTEXT_ONLY_TYPES:
                score -= 0.40
            score += 0.08 * _safe_float(x.get("rank_score"))
            return score

        items = sorted(items or [], key=item_score, reverse=True)
        return _dedupe_items(items, max_items=max_items)

    # ------------------------------------------------------------------
    # Nettoyage / tri / sorties
    # ------------------------------------------------------------------
    def _filter_to_validate_when_explicit_exists(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Quand un verrou explicite existe, ne garder que les meilleurs compléments."""
        out: List[Dict[str, Any]] = []
        for x in items or []:
            fr = x.get("frascati") or {}
            signals = fr.get("signals") or {}
            theme_id = str(x.get("theme_id") or "")
            src = str(x.get("_source_category") or "")

            # Garder quelques thèmes universels forts.
            if x.get("verrou_source") in {"universal_theme_reconstruction", "implicit_theme_reconstruction"}:
                if theme_id in {
                    "qualite_sortie_non_conforme",
                    "instabilite_comportement",
                    "contrainte_thermique",
                    "adaptation_contexte_specifique",
                    "performance_insuffisante",
                }:
                    out.append(x)
                continue

            # Rejeter méthodes/paramètres/contributions faibles.
            if src in BAD_AS_VERROU_CATEGORIES:
                if _safe_float(fr.get("frascati_score")) >= 0.55 and signals.get("problem_hits", 0) >= 1:
                    out.append(x)
                continue

            # Garder limites / résultats problématiques utiles.
            if signals.get("problem_hits", 0) >= 1 and _safe_float(fr.get("frascati_score")) >= 0.45:
                out.append(x)

        return out

    def _sort_and_dedupe(self, items: List[Dict[str, Any]], max_items: int) -> List[Dict[str, Any]]:
        def score(x: Dict[str, Any]) -> float:
            fr = x.get("frascati") or {}
            return _safe_float(fr.get("frascati_score")) + 0.25 * _safe_float(x.get("rank_score"))

        items = sorted(items or [], key=score, reverse=True)
        out = _dedupe_items(items, max_items=max_items)
        for i, item in enumerate(out, start=1):
            item.setdefault("cluster_id", f"frascati_verrou_{i:03d}")
            item["text"] = _short(item.get("text") or "")
        return out

    def _fallback_role_for_rejected(self, item: Dict[str, Any]) -> str:
        original = str(item.get("original_role") or item.get("role") or "")
        src = str(item.get("_source_category") or "")
        text = _clean(item.get("text") or "")
        if "methode" in src or _has(METHOD_OR_CONTEXT_PATTERNS, text):
            return "methode_ou_protocole"
        if "resultat" in src or _matches(EVIDENCE_PATTERNS, text) >= 2:
            return "resultat_ou_preuve"
        if "parametre" in src:
            return "parametre_ou_contrainte"
        if "contribution" in src:
            return "contribution_ou_indicateur"
        if original and original != "verrou":
            return original
        return "contexte_non_verrou"

    def _build_qualified_pack(
        self,
        original_pack: Dict[str, Any],
        verrous_probables: List[Dict[str, Any]],
        verrous_a_verifier: List[Dict[str, Any]],
        faux_verrous_rejetes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key in PACK_KEYS:
            if key == "verrous_rnd_locaux":
                continue
            out[key] = list(original_pack.get(key, []) or [])

        verrous_potentiels = _dedupe_items(verrous_probables + verrous_a_verifier, max_items=24)
        out["verrous_rnd_locaux"] = verrous_potentiels
        out["verrous_potentiels_consultant"] = verrous_potentiels
        out["verrous_probables_frascati"] = verrous_probables
        out["verrous_a_verifier_frascati"] = verrous_a_verifier
        out["faux_verrous_rejetes_frascati"] = faux_verrous_rejetes
        return out

    def _risk_report(
        self,
        probables: List[Dict[str, Any]],
        a_verifier: List[Dict[str, Any]],
        rejected: List[Dict[str, Any]],
        documents: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        docs_total = len(documents or [])
        docs_used = Counter(x.get("document") for x in (probables + a_verifier) if x.get("document"))
        total_verrous = len(probables) + len(a_verifier)

        scores = []
        for x in (probables + a_verifier):
            fr = x.get("frascati") or {}
            scores.append(_safe_float(fr.get("frascati_score")))
        global_score = round(sum(scores) / len(scores), 4) if scores else 0.0

        if len(probables) >= 1 and total_verrous >= 2 and global_score >= 0.52:
            risk = "moyen_faible"
        elif total_verrous >= 2:
            risk = "moyen"
        else:
            risk = "élevé"

        notes = []
        if rejected:
            notes.append("Des faux verrous probables ont été détectés : vérifier que méthodes/mesures/contraintes ne soient pas présentées comme verrous.")
        if not probables and a_verifier:
            notes.append("Aucun verrou fort n'est confirmé automatiquement, mais des verrous R&D potentiels sont reconstruits et doivent être validés avec le consultant.")
        if docs_total and len(docs_used) < max(1, min(2, docs_total)):
            notes.append("Les verrous qualifiés reposent sur peu de documents : vérifier la couverture documentaire.")
        if not notes:
            notes.append("Qualification Frascati préliminaire exploitable, sous validation humaine.")

        return {
            "risk_level": risk,
            "global_frascati_score": global_score,
            "global_frascati_percentage": round(global_score * 100, 2),
            "probable_verrous_count": len(probables),
            "verrous_to_validate_count": len(a_verifier),
            "rejected_false_verrous_count": len(rejected),
            "documents_supporting_verrous": dict(docs_used),
            "notes": notes,
        }

    def _consultant_view(
        self,
        probables: List[Dict[str, Any]],
        a_verifier: List[Dict[str, Any]],
        rejected: List[Dict[str, Any]],
        risk_report: Dict[str, Any],
    ) -> Dict[str, Any]:
        candidates = _dedupe_items((probables or []) + (a_verifier or []), max_items=20)
        scores = [_safe_float((x.get("frascati") or {}).get("frascati_score")) for x in candidates]
        avg_score = round(sum(scores) / len(scores), 4) if scores else 0.0

        if len(probables or []) >= 1:
            display_status = "verrou_rnd_probable_detecte"
            display_risk = risk_report.get("risk_level", "moyen")
            message = "Au moins un verrou R&D probable est détecté. Les autres signaux doivent être validés par le consultant."
        elif len(candidates) >= 2:
            display_status = "potentiel_cir_a_expertiser"
            display_risk = "moyen"
            message = "Des verrous R&D potentiels ont été reconstruits à partir des documents bruts, mais ils doivent être validés par le consultant."
        elif len(candidates) >= 1:
            display_status = "signaux_rnd_a_verifier"
            display_risk = risk_report.get("risk_level", "moyen")
            message = "Un signal R&D existe, mais la preuve documentaire reste partielle."
        else:
            display_status = "aucun_verrou_rnd_exploitable_detecte"
            display_risk = "élevé"
            message = "Aucun verrou R&D exploitable n'est détecté dans les documents fournis."

        return {
            "display_title": "Verrous R&D potentiels à valider consultant",
            "display_status": display_status,
            "display_risk_level": display_risk,
            "main_message": message,
            "strict_probable_verrous_count": len(probables or []),
            "to_validate_verrous_count": len(a_verifier or []),
            "potential_verrous_count": len(candidates),
            "rejected_false_verrous_count": len(rejected or []),
            "average_potential_frascati_score": avg_score,
            "average_potential_frascati_percentage": round(avg_score * 100, 2),
            "important_note": (
                "Un verrou à vérifier n'est pas un rejet. Dans un pré-diagnostic sur documents bruts, "
                "il indique un verrou possible à reformuler et valider humainement."
            ),
        }

    def _questions_consultant(
        self,
        probables: List[Dict[str, Any]],
        a_verifier: List[Dict[str, Any]],
        rejected: List[Dict[str, Any]],
    ) -> List[str]:
        questions = [
            "Quel était l'état de l'art ou la solution connue avant le projet ?",
            "Quelle incertitude technique ou scientifique ne pouvait pas être résolue par une simple application de méthodes connues ?",
            "Quelles hypothèses, essais, itérations ou comparaisons ont été réellement menés ?",
            "Quels résultats prouvent que les travaux répondent aux verrous identifiés ?",
        ]
        if a_verifier:
            questions.append("Parmi les verrous à vérifier, lesquels le consultant souhaite-t-il conserver, reformuler ou supprimer ?")
        if rejected:
            questions.append("Les éléments rejetés comme faux verrous sont-ils seulement des méthodes/mesures/contraintes, ou cachent-ils une incertitude non explicitée ?")
        return questions

# -----------------------------------------------------------------------------
# API publique compatible
# -----------------------------------------------------------------------------

def apply_frascati_guard(
    pack: Optional[Dict[str, Any]] = None,
    documents: Optional[List[Dict[str, Any]]] = None,
    mode: str = "raw_construction",
    domain: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Signature flexible pour éviter les erreurs de type :
    - apply_frascati_guard(pack=...)
    - apply_frascati_guard(evidence_pack=...)
    - apply_frascati_guard(pack_before_frascati=...)
    - apply_frascati_guard(documents=..., domain=...)
    """
    if pack is None:
        pack = (
            kwargs.get("evidence_pack")
            or kwargs.get("pack_before_frascati")
            or kwargs.get("evidence_pack_for_ennodiagnostic")
            or kwargs.get("qualified_pack_for_ennodiagnostic")
            or {}
        )
    if documents is None:
        documents = kwargs.get("docs") or kwargs.get("input_documents") or []
    if domain is None:
        domain = kwargs.get("domain_result") or kwargs.get("detected_domain") or kwargs.get("domain_detection") or {}

    guard = FrascatiGuard(mode=mode, domain=domain)
    return guard.qualify(pack=pack or {}, documents=documents or [])