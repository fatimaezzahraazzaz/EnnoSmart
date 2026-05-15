"""
modules/NLP/evidence_mapper.py — LE CŒUR DU PIPELINE
──────────────────────────────────────────────────────────────────────────────
Approche evidence-first : on ne classe plus des MOTS, on classe la FONCTION
ARGUMENTATIVE des PASSAGES.

  segmenter.py ──► evidence_mapper.py ──► aggregator.py

VERSION 2.0 - UNIVERSELLE
-------------------------
- Modèle par défaut : qwen2.5:7b-instruct (plus robuste)
- Signaux de détection élargis pour TOUS les domaines
- Détection des verrous : termes universels (contrainte, blocage, défi, gap)
- Détection des démarches : verbes d'action génériques
- Fonctionne pour : informatique, mécanique, chimie, biologie, électronique, etc.

Version : 2.0.0-universal
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    import ollama
except ImportError:
    ollama = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

logger = logging.getLogger(__name__)

# CHANGÉ : modèle plus robuste et universel
DEFAULT_LLM_MODEL = "ollama:qwen2.5:7b-instruct"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
LOCAL_MODEL_PREFIXES = ("ollama:", "local:")

MAX_PASSAGE_CHARS = 3200
MAX_RETRIES = 1
TIMEOUT_SECONDS = 90

# Rôles enrichis
VALID_ROLES = {
    "contexte",
    "objectif",
    "verrou",
    "etat_art",
    "demarche",
    "essai",
    "resultat",
    "preuve",
    "metrique",        # NOUVEAU : pour les données chiffrées, KPIs
    "administratif",
    "hors_sujet",
}

DIAGNOSTIC_ROLES = {
    "contexte", "objectif", "verrou", "etat_art",
    "demarche", "essai", "resultat", "preuve", "metrique",
}

MAX_LLM_PASSAGES = 14
MAX_EVIDENCES_PER_PASSAGE = 4
MIN_PASSAGE_CHARS_FOR_LLM = 80

SKIP_SECTION_ROLES = {
    "annexe",
    "administratif",
    "financial_admin",
}

PRIORITY_SECTION_SCORES = {
    "verrous": 100,
    "objectifs": 95,
    "resultats": 92,
    "demarche": 90,
    "travaux": 88,
    "etat_art": 80,
    "contexte": 55,
    "unknown": 50,
    "titre_motscles": 15,
    "annexe": -50,
    "administratif": -60,
    "financial_admin": -60,
}

# Signal universel de présence de contenu R&D (élargi)
EVIDENCE_SIGNAL_RE = re.compile(
    r"(objectif|vise|vis[ée]|probl[èe]me|probl[ée]matique|verrou|incertitude|"
    r"limite|difficult[ée]|ne permet pas|n['']arrive pas|incapacit[ée]|"
    r"contrainte|manque|d[ée]fi|blocage|obstacle|gap|"
    r"[ée]tat de l['']art|solution|prototype|"
    r"essai|test|validation|mesur|compar|r[ée]sultat|satisfaisant|[ée]chec|"
    r"succ[èe]s|retenu|retenue|brevet|innov|R&D|recherche|d[ée]veloppement|"
    r"simulation|mod[ée]lisation|protocole|norme|méthode|démarche|approche)",
    re.I | re.U,
)

TOC_OR_TITLE_ONLY_RE = re.compile(
    r"^\s*(?:"
    r"table des mati[èe]res|sommaire|"
    r"(?:\d+(?:\.\d+)*\.?\s*)?"
    r"(?:objectifs?|contexte|analyse de l['']?[ée]tat de l['']?art|"
    r"verrous?.*|raisonnement scientifique.*|r[ée]sultats? de R&D|"
    r"conclusion.*|indicateurs? de R&D|description des ressources humaines|annexes?|"
    r"intitul[ée] de l['']op[ée]ration|op[ée]ration de R&D.*)"
    r"\s*\d{0,3}\s*)$",
    re.I | re.U,
)

ADMIN_SIGNAL_RE = re.compile(
    r"(nom pr[ée]nom|dipl[oô]me|fonction dans l['']op[ée]ration|"
    r"co[uû]t total|co[uû]t d[ée]clar[ée]|date de d[ée]but|date de fin|"
    r"num[ée]ro de t[ée]l[ée]phone|adresse [ée]lectronique|rescrit CIR|agr[ée]ment)",
    re.I | re.U,
)

# ── Détecteurs d'artefacts et de faux verrous ─────────────────────────────────

_ARTIFACT_PHRASE_RE = re.compile(
    r"^(?:"
    r"[a-z]\s+"                                                          # minuscule isolée
    r"|(?:et|ou|mais|donc|car|ni|or|que|qui|dont|où|si|bien|ainsi)\s"  # conjonction début
    r"|(?:de|du|des|le|la|les|un|une|en|au|aux)\s[a-z]"                 # article + minuscule
    r")",
    re.UNICODE,
)

# Phrases de justification administrative CIR
_ADMIN_CIR_JUSTIF_RE = re.compile(
    r"(manuel de frascati|crit[eè]res? d[''']?[ée]ligibilit[ée]|"
    r"s[ée]lectionn[ée]\s+en\s+se\s+basant|crit[eè]res?\s+suivants?|"
    r"d[ée]crit\s+dans\s+le\s+tableau|rescrit\s+CIR|agr[ée]ment\s+CIR|"
    r"OCDE|Organisation\s+de\s+Coop[ée]ration)",
    re.I | re.U,
)

# UNIVERSEL : phrases de spécification ou solution (pas des verrous)
_SPEC_NOT_VERROU_RE = re.compile(
    r"(?:"
    r"nous avons d[ée]fini|nous avons retenu|nous avons choisi|"
    r"notre architecture|notre solution|notre approche|"
    r"consiste à|comporte une|"
    r"paramètre de|valeur de|dimension de|"
    r"calculée par|déterminée par"
    r")",
    re.I | re.U,
)


def _passage_get(passage: Any, attr: str, default: Any = "") -> Any:
    if isinstance(passage, dict):
        return passage.get(attr, default)
    return getattr(passage, attr, default)


def _is_probable_toc_or_title_only(text: str, section_role: str = "") -> bool:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean:
        return True
    if len(clean) < MIN_PASSAGE_CHARS_FOR_LLM and TOC_OR_TITLE_ONLY_RE.match(clean):
        return True
    lines = [l.strip() for l in str(text or "").splitlines() if l.strip()]
    if len(lines) >= 6:
        toc_hits = sum(1 for l in lines if re.search(r"\s\d{1,3}$", l) or TOC_OR_TITLE_ONLY_RE.match(l))
        if toc_hits / max(len(lines), 1) >= 0.55:
            return True
    if section_role == "titre_motscles" and len(clean) < 220 and not EVIDENCE_SIGNAL_RE.search(clean):
        return True
    return False


def _score_passage_for_llm(passage: Any) -> int:
    text = str(_passage_get(passage, "text", "") or "")
    section_role = str(_passage_get(passage, "section_role", "unknown") or "unknown")
    section_title = str(_passage_get(passage, "section_title", "") or "")

    if _is_probable_toc_or_title_only(text, section_role):
        return -999
    if section_role in SKIP_SECTION_ROLES:
        if not EVIDENCE_SIGNAL_RE.search(text):
            return -999

    score = PRIORITY_SECTION_SCORES.get(section_role, 40)
    signal_count = len(EVIDENCE_SIGNAL_RE.findall(text))
    score += min(signal_count * 8, 60)

    if ADMIN_SIGNAL_RE.search(text):
        score -= 45
    if "table des matières" in text.lower() or "sommaire" in text.lower():
        score -= 90

    if len(text) > 500:
        score += 10
    if len(text) > 1800:
        score += 5

    if re.search(r"objectif|verrou|d[ée]marche|travaux|r[ée]sultat|essai|test", section_title, re.I):
        score += 15

    return score


def _select_passages_for_llm(
    passages: list[Any],
    max_passages: int = MAX_LLM_PASSAGES,
) -> tuple[list[Any], list["PassageMapping"]]:
    scored: list[tuple[int, int, Any]] = []
    skipped_mappings: list[PassageMapping] = []

    for i, p in enumerate(passages or []):
        text = str(_passage_get(p, "text", "") or "")
        passage_id = str(_passage_get(p, "passage_id", "") or f"p{i}")
        section_role = str(_passage_get(p, "section_role", "unknown") or "unknown")
        score = _score_passage_for_llm(p)

        if score <= -900:
            role = (
                "administratif"
                if section_role in {"administratif", "financial_admin"} or ADMIN_SIGNAL_RE.search(text)
                else "hors_sujet"
            )
            skipped_mappings.append(PassageMapping(
                passage_id=passage_id,
                roles_cir=[role],
                evidences=[],
                concepts=[],
                error=None,
            ))
            continue
        scored.append((score, i, p))

    scored.sort(key=lambda x: (-x[0], x[1]))
    selected: list[Any] = []
    selected_ids: set[str] = set()

    preferred_roles = ["objectifs", "verrous", "demarche", "travaux", "resultats", "etat_art", "contexte", "unknown"]
    for role in preferred_roles:
        for score, i, p in scored:
            pid = str(_passage_get(p, "passage_id", "") or f"p{i}")
            if pid in selected_ids:
                continue
            if str(_passage_get(p, "section_role", "unknown") or "unknown") == role:
                selected.append(p)
                selected_ids.add(pid)
                break

    for score, i, p in scored:
        if len(selected) >= max_passages:
            break
        pid = str(_passage_get(p, "passage_id", "") or f"p{i}")
        if pid not in selected_ids:
            selected.append(p)
            selected_ids.add(pid)

    order = {str(_passage_get(p, "passage_id", "") or f"p{i}"): i for i, p in enumerate(passages or [])}
    selected.sort(key=lambda p: order.get(str(_passage_get(p, "passage_id", "")), 10**9))

    return selected, skipped_mappings


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Evidence:
    role: str
    phrase_source: str
    passage_id: str
    section_role: str = "unknown"
    confidence: float = 0.7
    validated: bool = False

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "phrase_source": self.phrase_source,
            "passage_id": self.passage_id,
            "section_role": self.section_role,
            "confidence": round(float(self.confidence), 3),
            "validated": self.validated,
        }


@dataclass
class PassageMapping:
    passage_id: str
    roles_cir: list[str] = field(default_factory=list)
    evidences: list[Evidence] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "passage_id": self.passage_id,
            "roles_cir": self.roles_cir,
            "evidences": [e.to_dict() for e in self.evidences],
            "concepts": self.concepts,
            "error": self.error,
        }


@dataclass
class EvidenceMapResult:
    mappings: list[PassageMapping] = field(default_factory=list)
    llm_calls: int = 0
    backend: str = "unknown"
    model: str = ""
    processing_time: float = 0.0
    errors: list[str] = field(default_factory=list)

    def all_evidences(self) -> list[Evidence]:
        out: list[Evidence] = []
        for m in self.mappings:
            out.extend(m.evidences)
        return out

    def all_concepts(self) -> list[str]:
        out: list[str] = []
        for m in self.mappings:
            out.extend(m.concepts)
        return out

    def to_dict(self) -> dict:
        return {
            "mappings": [m.to_dict() for m in self.mappings],
            "stats": {
                "passages": len(self.mappings),
                "passages_with_llm": self.llm_calls,
                "passages_skipped": max(len(self.mappings) - self.llm_calls, 0),
                "llm_calls": self.llm_calls,
                "backend": self.backend,
                "model": self.model,
                "processing_time": round(self.processing_time, 2),
                "total_evidences": len(self.all_evidences()),
                "errors": len(self.errors),
            },
            "errors": self.errors,
        }


# ══════════════════════════════════════════════════════════════════════════════
# PROMPTS - VERSION UNIVERSELLE
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """Tu analyses un passage extrait d'un document lié à un projet de R&D.

Le domaine peut être N'IMPORTE LEQUEL : informatique, biologie, médical,
militaire, automobile, chimie, mécanique, architecture, matériaux, énergie,
électronique, etc. Tu ne dois RIEN supposer sur le domaine.

Ta seule tâche : identifier la FONCTION ARGUMENTATIVE des phrases du passage,
c'est-à-dire à quoi elles servent dans un raisonnement de R&D.

LES 11 RÔLES POSSIBLES (n'utilise que ceux-ci) :
- "contexte"      : cadre du projet, présentation, problématique générale.
- "objectif"      : ce que le projet cherche à atteindre, à développer, à améliorer.
- "verrou"        : une difficulté, une incertitude, une limite, un blocage technique,
                    quelque chose qu'on ne sait pas encore faire ou résoudre.
- "etat_art"      : des travaux existants, des solutions déjà connues, une comparaison
                    avec ce que d'autres ont fait, de la bibliographie.
- "demarche"      : une méthode, un protocole, un plan de travail, une approche choisie
                    pour avancer. Verbes typiques : avons, avons réalisé, avons développé,
                    avons mis en place, avons configuré, avons mesuré.
- "essai"         : un test, une expérimentation, une mesure, une validation menée.
- "resultat"      : un résultat obtenu, une performance atteinte, une observation finale.
- "preuve"        : un élément justificatif explicite, une donnée qui prouve.
- "metrique"      : une donnée chiffrée, un KPI, un pourcentage, une mesure quantitative.
- "administratif" : budget, ressources humaines, planning, organisme, gestion interne.
- "hors_sujet"    : le passage ne contient rien d'utile pour comprendre la R&D.

RÈGLES ABSOLUES :
1. Réponds UNIQUEMENT en JSON valide. Aucun texte avant ou après.
2. "phrase_source" doit être une phrase qui existe TEXTUELLEMENT dans le passage.
   Recopie-la exactement, ne la reformule pas, ne la résume pas.
3. N'invente aucun concept. Les concepts techniques que tu listes doivent
   apparaître tels quels dans le passage.
4. Ne devine pas le domaine. Ne complète pas avec des connaissances extérieures.
5. Un même passage peut avoir plusieurs rôles.
6. Si le passage ne contient rien d'utile : roles_cir = ["hors_sujet"], evidences = [].

RÈGLES SPÉCIFIQUES :
- "verrou" : exprime une impossibilité, une limite, un risque non résolu.
  Mots typiques : ne permet pas, n'arrive pas, incapacité, difficulté, risque,
  contrainte, blocage, défi, gap, manque, absence.
- "demarche" : exprime une action réalisée. Verbes typiques : nous avons, a été,
  ont été, mis en place, développé, configuré, mesuré, testé, comparé.
- "metrique" : toute donnée chiffrée avec unité (%, °C, N, Pa, Hz, ms, €...).

FORMAT JSON ATTENDU :
{
  "roles_cir": ["objectif", "verrou"],
  "evidences": [
    {"role": "objectif", "phrase_source": "<phrase exacte du passage>"},
    {"role": "verrou", "phrase_source": "<phrase exacte du passage>"}
  ],
  "concepts": ["<terme technique recopié>", "<autre terme recopié>"]
}
"""

USER_PROMPT_TEMPLATE = """{hint}

# PASSAGE À ANALYSER

{text}

# CONSIGNE
Identifie la fonction argumentative des phrases de ce passage.
Recopie les phrases exactes comme preuves.
Pour "demarche", cherche les verbes d'action au passé (nous avons, a été réalisé, etc.).
Pour "verrou", cherche les expressions de blocage ou difficulté.
Pour "metrique", cherche les chiffres avec unités.
Réponds uniquement avec le JSON strict.
"""


# ══════════════════════════════════════════════════════════════════════════════
# UTILITAIRES
# ══════════════════════════════════════════════════════════════════════════════

def _norm_for_match(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("'", "'").replace("œ", "oe").replace("æ", "ae")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _phrase_in_text(phrase: str, full_text: str) -> bool:
    p = _norm_for_match(phrase)
    t = _norm_for_match(full_text)
    if not p or len(p) < 8:
        return False
    if p in t:
        return True
    words = [w for w in p.split() if len(w) > 2]
    if len(words) < 3:
        return False
    hits = sum(1 for w in words if w in t)
    return hits / len(words) >= 0.85


def _is_local_model(model: str) -> bool:
    return str(model or "").startswith(LOCAL_MODEL_PREFIXES)


def _clean_local_model_name(model: str) -> str:
    model = str(model or DEFAULT_LLM_MODEL).strip()
    for prefix in LOCAL_MODEL_PREFIXES:
        if model.startswith(prefix):
            return model[len(prefix):]
    return model


def _get_openrouter_client():
    if OpenAI is None:
        return None
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)


def _extract_json(content: str) -> Optional[dict]:
    raw = str(content or "").strip()
    if not raw:
        return None
    candidates = [
        raw,
        re.sub(r"```(?:json)?|```", "", raw, flags=re.I).strip(),
    ]
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        candidates.append(m.group(0))
    for c in candidates:
        try:
            data = json.loads(c)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None


_OLLAMA_SCHEMA = {
    "type": "object",
    "properties": {
        "roles_cir": {"type": "array", "items": {"type": "string"}},
        "evidences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string"},
                    "phrase_source": {"type": "string"},
                },
                "required": ["role", "phrase_source"],
            },
        },
        "concepts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["roles_cir", "evidences", "concepts"],
}


# ══════════════════════════════════════════════════════════════════════════════
# APPELS LLM
# ══════════════════════════════════════════════════════════════════════════════

def _call_ollama(text: str, hint: str, model: str, retry: int = 0) -> Optional[dict]:
    if ollama is None:
        logger.error("ollama non installé : pip install ollama")
        return None
    local_model = _clean_local_model_name(model)
    try:
        response = ollama.chat(
            model=local_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_PROMPT_TEMPLATE.format(hint=hint, text=text)},
            ],
            format=_OLLAMA_SCHEMA,
            options={
                "temperature": 0,
                "top_p": 0.1,
                "num_ctx": 16384,  # AUGMENTÉ : meilleure mémoire
                "num_predict": 900,
            },
        )
        content = response.get("message", {}).get("content", "")
        data = _extract_json(content)
        if data is None:
            logger.warning("JSON Ollama invalide. Brut : %s", str(content)[:400])
            if retry < MAX_RETRIES:
                time.sleep(1.0)
                return _call_ollama(text, hint, model, retry + 1)
            return None
        return data
    except Exception as exc:
        logger.exception("Erreur Ollama (retry=%d) : %s", retry, exc)
        if retry < MAX_RETRIES:
            time.sleep(1.0)
            return _call_ollama(text, hint, model, retry + 1)
        return None


def _call_openrouter(text: str, hint: str, model: str) -> Optional[dict]:
    client = _get_openrouter_client()
    if client is None:
        return None
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_PROMPT_TEMPLATE.format(hint=hint, text=text)},
            ],
            temperature=0,
            max_tokens=1500,
            timeout=TIMEOUT_SECONDS,
        )
        content = completion.choices[0].message.content
        data = _extract_json(content)
        if data is None:
            logger.warning("JSON OpenRouter invalide. Brut : %s", str(content)[:400])
            return None
        return data
    except Exception as exc:
        logger.exception("Erreur OpenRouter : %s", exc)
        return None


def _call_llm(text: str, hint: str, model: str) -> tuple[Optional[dict], str]:
    if _is_local_model(model):
        return _call_ollama(text, hint, model), "ollama"
    return _call_openrouter(text, hint, model), "openrouter"


# ══════════════════════════════════════════════════════════════════════════════
# PARSING ET VALIDATION - VERSION UNIVERSELLE
# ══════════════════════════════════════════════════════════════════════════════

def _is_artifact_phrase(phrase: str) -> bool:
    """Détecte les phrases artefacts issues d'overlap de segmentation."""
    phrase = str(phrase or "").strip()

    if len(phrase) < 20:
        logger.debug("Evidence rejetée (trop court < 20 chars) : %r", phrase)
        return True

    if _ARTIFACT_PHRASE_RE.match(phrase):
        logger.debug("Evidence rejetée (artefact de coupure) : %r", phrase[:60])
        return True

    return False


def _is_false_verrou(phrase: str) -> bool:
    """
    UNIVERSEL : vérifie qu'une phrase classée 'verrou' n'est pas
    une spécification technique ou une justification admin.
    """
    phrase = str(phrase or "").strip()

    if _ADMIN_CIR_JUSTIF_RE.search(phrase):
        logger.debug("Faux verrou (justification admin CIR) : %r", phrase[:80])
        return True

    if _SPEC_NOT_VERROU_RE.search(phrase):
        logger.debug("Faux verrou (spec technique / solution définie) : %r", phrase[:80])
        return True

    return False


def _is_allowed_phrase_for_role(phrase: str, role: str) -> bool:
    """
    UNIVERSEL : vérifie qu'une phrase est acceptable pour un rôle donné.
    Utilisé dans le parsing pour filtrer les mauvais classements.
    """
    if _is_bad_structural_phrase(phrase):
        return False

    low = _norm_for_match(phrase)

    if role == "verrou":
        return bool(re.search(
            r"(?:verrou|incapacit|ne permet pas|ne permettent pas|ne pouvons pas|"
            r"difficult|risque|probl[èe]matique|limite|manque|insuffisant|"
            r"toutefois|cependant|ne parvient pas|ne résout pas|"
            r"reste [àa] d[ée]passer|d[ée]fi|contrainte|blocage|obstacle|"
            r"gap|foss[ée]|manque de|absence de|insuffisance de|"
            r"ne ma[îi]trise pas|ne contr[ôo]le pas|ne sait pas|"
            r"ne fonctionne pas|échoue|défaut|faiblesse|inconvénient)",
            low,
            re.I,
        )) and len(low.split()) >= 5

    if role == "demarche":
        if re.search(r"r[ée]sultats? .* ont permis|performances .* ont port[ée]|sont positifs", low, re.I):
            if not re.search(r"\b(?:nous avons|ont été|a été réalisé)\b", low, re.I):
                return False
        
        return bool(re.search(
            r"\b(?:"
            r"nous avons|nous avons mis en place|nous avons d[ée]velopp[ée]|"
            r"a été r[ée]alis[ée]|ont [ée]t[ée] r[ée]alis[ée]s|"
            r"m[ée]thode|protocole|d[ée]marche|approche|strat[ée]gie|"
            r"benchmarking|pipeline|processus|procédure|"
            r"configuration|paramétrage|installation|mise en [œoe]uvre|"
            r"test[ée]|mesur[ée]|compar[ée]|analys[ée]|évalu[ée]|"
            r"simulation|mod[ée]lisation|conception|développement|"
            r"validation|vérification|calibration|étalonnage"
            r")\b",
            low,
            re.I,
        )) and len(low.split()) >= 5

    if role == "metrique":
        return bool(re.search(
            r"\d+(?:[,.]\d+)?\s*(?:%|°C|N|Pa|Hz|ms|s|mn|h|mm|cm|m|"
            r"kg|g|mg|V|A|W|Ω|€|USD|EUR|fois|X|ratio|taux|score|"
            r"couverture|précision|rappel|f1|accuracy)",
            low,
            re.I,
        ))

    return True


def _is_bad_structural_phrase(phrase: str) -> bool:
    """Filtre les phrases structurelles non-informatives."""
    p = re.sub(r"\s+", " ", str(phrase or "")).strip()
    if not p:
        return True
    if len(p) < 25:
        return True
    if re.search(r"table des mati[èe]res|sommaire", p, re.I):
        return True
    return False


def _parse_passage_response(
    data: dict,
    passage_text: str,
    passage_id: str,
    section_role: str,
) -> PassageMapping:
    """
    Parse + valide la réponse LLM pour un passage.
    Version universelle avec support du rôle 'metrique'.
    """
    mapping = PassageMapping(passage_id=passage_id)

    # Rôles
    raw_roles = data.get("roles_cir", []) or []
    roles = []
    for r in raw_roles:
        r = str(r or "").strip().lower()
        if r in VALID_ROLES:
            roles.append(r)
    mapping.roles_cir = list(dict.fromkeys(roles)) or ["hors_sujet"]

    # Evidences
    kept_evidence_count = 0
    for ev in data.get("evidences", []) or []:
        if kept_evidence_count >= MAX_EVIDENCES_PER_PASSAGE:
            break
        if not isinstance(ev, dict):
            continue

        role = str(ev.get("role", "") or "").strip().lower()
        phrase = str(ev.get("phrase_source", "") or "").strip()

        if role not in VALID_ROLES or not phrase:
            continue

        # Filtre artefacts
        if _is_artifact_phrase(phrase):
            continue

        # Filtre faux verrous avec reclassement
        if role == "verrou" and _is_false_verrou(phrase):
            if _ADMIN_CIR_JUSTIF_RE.search(phrase):
                role = "administratif"
                logger.debug("Reclassé verrou→administratif : %r", phrase[:60])
            elif _SPEC_NOT_VERROU_RE.search(phrase):
                role = "demarche"
                logger.debug("Reclassé verrou→demarche : %r", phrase[:60])

        # Anti-hallucination
        if not _phrase_in_text(phrase, passage_text):
            logger.debug("Evidence rejetée (absent du passage) : %r", phrase[:80])
            continue

        mapping.evidences.append(
            Evidence(
                role=role,
                phrase_source=phrase,
                passage_id=passage_id,
                section_role=section_role,
                confidence=0.8 if section_role != "unknown" and section_role.startswith(role[:4]) else 0.7,
                validated=True,
            )
        )
        kept_evidence_count += 1

    # Concepts
    norm_text = _norm_for_match(passage_text)
    for concept in data.get("concepts", []) or []:
        concept = str(concept or "").strip()
        if not concept or len(concept) < 2:
            continue
        if _norm_for_match(concept) in norm_text:
            mapping.concepts.append(concept)
    mapping.concepts = list(dict.fromkeys(mapping.concepts))

    return mapping


# ══════════════════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ══════════════════════════════════════════════════════════════════════════════

def map_evidence(
    passages: list[Any],
    model: str = DEFAULT_LLM_MODEL,
    enabled: bool = True,
) -> EvidenceMapResult:
    """
    Mappe chaque passage vers ses rôles CIR + preuves.

    Paramètres
    ----------
    passages : list[Passage]  (depuis segmenter.segment_chunks)
    model    : modèle LLM (par défaut: qwen2.5:7b-instruct)
    enabled  : si False, retourne un résultat vide

    Retourne
    --------
    EvidenceMapResult
    """
    result = EvidenceMapResult()
    result.model = model
    t0 = time.time()

    if not enabled or not passages:
        result.processing_time = time.time() - t0
        return result

    backend_used = "unknown"

    selected_passages, skipped_mappings = _select_passages_for_llm(passages, MAX_LLM_PASSAGES)
    result.mappings.extend(skipped_mappings)

    for passage in selected_passages:
        text = str(getattr(passage, "text", "") or "").strip()
        passage_id = str(getattr(passage, "passage_id", "") or f"p{len(result.mappings)}")
        section_role = str(getattr(passage, "section_role", "unknown") or "unknown")

        if not text:
            continue
        if len(text) > MAX_PASSAGE_CHARS:
            text = text[:MAX_PASSAGE_CHARS] + "\n[... passage tronqué par sécurité ...]"

        hint = ""
        if hasattr(passage, "context_hint"):
            try:
                hint = passage.context_hint()
            except Exception:
                hint = ""
        elif section_role != "unknown":
            hint = f"[indice_section: {section_role}]"

        data, backend = _call_llm(text, hint, model)
        backend_used = backend
        result.llm_calls += 1

        if data is None:
            msg = f"Passage {passage_id} : aucune réponse LLM exploitable"
            logger.warning(msg)
            result.errors.append(msg)
            result.mappings.append(PassageMapping(passage_id=passage_id, error="no_llm_response"))
            continue

        try:
            mapping = _parse_passage_response(data, text, passage_id, section_role)
            result.mappings.append(mapping)
        except Exception as exc:
            logger.exception("Erreur parsing passage %s : %s", passage_id, exc)
            result.errors.append(f"Parsing {passage_id}: {type(exc).__name__}: {exc}")
            result.mappings.append(PassageMapping(passage_id=passage_id, error=str(exc)))

    result.backend = backend_used
    result.processing_time = time.time() - t0

    logger.info(
        "Evidence mapping [%s:%s] : %d passages | %d appels | %d preuves | %d erreurs",
        result.backend, result.model,
        len(result.mappings), result.llm_calls,
        len(result.all_evidences()), len(result.errors),
    )
    return result


# ── Interface rapide (debug / tests) ──────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    print("=== Evidence Mapper - Version Universelle 2.0 ===")
    print(f"Modèle par défaut : {DEFAULT_LLM_MODEL}")
    print()

    print("=== Test 1 : anti-hallucination basique ===")
    sample_text = (
        "L'objectif de ce projet est de développer un emballage médical recyclable. "
        "Le verrou principal est l'incapacité de résoudre simultanément la tenue aux "
        "chocs et la recyclabilité. Nous avons réalisé des essais de chute sur trois prototypes.\n"
        "Taux de réussite : 95%."
    )
    fake_response = {
        "roles_cir": ["objectif", "verrou", "essai", "metrique"],
        "evidences": [
            {"role": "objectif", "phrase_source": "L'objectif de ce projet est de développer un emballage médical recyclable."},
            {"role": "verrou", "phrase_source": "Le verrou principal est l'incapacité de résoudre simultanément la tenue aux chocs et la recyclabilité."},
            {"role": "essai", "phrase_source": "Nous avons réalisé des essais de chute sur trois prototypes."},
            {"role": "metrique", "phrase_source": "Taux de réussite : 95%."},
            {"role": "resultat", "phrase_source": "Le taux de réussite atteint 95 pour cent."},
        ],
        "concepts": ["emballage médical recyclable", "tenue aux chocs", "essais de chute"],
    }
    m = _parse_passage_response(fake_response, sample_text, "test_p0", "objectifs")
    print(f"Preuves : {len(m.evidences)}  (attendu: 4 - objectif, verrou, essai, metrique)")
    for e in m.evidences:
        print(f"  [{e.role}] {e.phrase_source[:70]}...")

    print("\n=== Test 2 : detection des faux verrous ===")
    cas = [
        ("Ce projet a été sélectionné sur les critères Frascati.", True),
        ("Nous avons défini une architecture en trois couches.", True),
        ("Toutefois, elles ne permettent pas l'absorption des chocs.", False),
        ("Le dispositif n'étant pas maintenu, un risque de chute existe.", False),
    ]
    for phrase, expected in cas:
        result_flag = _is_false_verrou(phrase)
        ok = "✓" if result_flag == expected else "✗"
        print(f"  {ok} faux_verrou={result_flag} | {phrase[:60]}")

    print("\n=== Test 3 : detection des artefacts ===")
    artifacts = [
        ("i 2015 établi par l'OCDE", True),
        ("et des résultats ont été obtenus", True),
        ("L'objectif principal est de développer", False),
    ]
    for phrase, expected in artifacts:
        result_flag = _is_artifact_phrase(phrase)
        ok = "✓" if result_flag == expected else "✗"
        print(f"  {ok} artefact={result_flag} | {phrase[:50]}")

    if len(sys.argv) > 1 and sys.argv[1] == "--live":
        from segmenter import segment_chunks
        passages = segment_chunks([sample_text], doc_id="live")
        res = map_evidence(passages, enabled=True)
        print("\n=== Test LIVE ===")
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))