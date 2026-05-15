"""
modules/NLP/synthesizer.py
──────────────────────────────────────────────────────────────────────────────
Synthèse finale du dossier R&D/CIR : fiche CIR + mots-clés.

Pipeline :
  aggregator.py + domain_classifier.py ──► synthesizer.py ──► NLPResult

Rôle :
- Faire UN SEUL appel LLM final sur les preuves agrégées.
- Produire une fiche CIR claire : objet, objectifs, verrous, état de l'art,
  démarche, essais, résultats.
- Corriger les oublis du LLM avec un fallback extractif.
- Filtrer les titres/sommaires/phrases introductives qui polluent la fiche.
- Éviter les fausses lacunes contredites par les preuves.
- UNIVERSEL : fonctionne pour tous les domaines (informatique, mécanique, chimie, biologie...)

Important :
- Ce fichier ne fait PAS de NER.
- Il ne contient PAS de listes métier fermées par domaine.
- Les mots-clés sont dérivés des preuves/concepts déjà extraits.

API :
    synthesize(aggregated, domain_classification=None, model=..., enabled=True) -> Synthesis

Version : 1.3.1-universal (corrigé)
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

DEFAULT_LLM_MODEL = "ollama:qwen2.5:7b-instruct"  # CHANGÉ : modèle plus robuste
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
LOCAL_MODEL_PREFIXES = ("ollama:", "local:")
TIMEOUT_SECONDS = 120
MAX_RETRIES = 1


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class FicheElement:
    """Un élément de fiche CIR : résumé + phrases-preuves exactes."""
    resume: str
    preuves: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"resume": self.resume, "preuves": self.preuves}


@dataclass
class Synthesis:
    """Fiche CIR complète + mots-clés."""
    domaine_principal: str = "non_classifié"
    domaine_detail: dict = field(default_factory=dict)

    objet_du_projet: Optional[FicheElement] = None
    objectifs: list[FicheElement] = field(default_factory=list)
    verrous: list[FicheElement] = field(default_factory=list)
    etat_art: list[FicheElement] = field(default_factory=list)
    demarche: list[FicheElement] = field(default_factory=list)  # Attention: singulier !
    essais: list[FicheElement] = field(default_factory=list)
    resultats: list[FicheElement] = field(default_factory=list)

    mots_cles: list[str] = field(default_factory=list)
    lacunes: list[str] = field(default_factory=list)

    llm_calls: int = 0
    backend: str = "unknown"
    model: str = ""
    processing_time: float = 0.0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "domaine_principal": self.domaine_principal,
            "domaine_detail": self.domaine_detail,
            "fiche_cir": {
                "objet_du_projet": self.objet_du_projet.to_dict() if self.objet_du_projet else None,
                "objectifs": [e.to_dict() for e in self.objectifs],
                "verrous": [e.to_dict() for e in self.verrous],
                "etat_art": [e.to_dict() for e in self.etat_art],
                "demarche": [e.to_dict() for e in self.demarche],
                "essais": [e.to_dict() for e in self.essais],
                "resultats": [e.to_dict() for e in self.resultats],
            },
            "mots_cles": self.mots_cles,
            "lacunes": self.lacunes,
            "stats": {
                "llm_calls": self.llm_calls,
                "backend": self.backend,
                "model": self.model,
                "processing_time": round(self.processing_time, 2),
                "errors": len(self.errors),
            },
            "errors": self.errors,
        }


# ══════════════════════════════════════════════════════════════════════════════
# PROMPT
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """Tu es un assistant qui rédige une fiche de synthèse R&D/CIR.

On te donne une synthèse de preuves déjà extraites d'un dossier :
phrases classées par rôle CIR et concepts techniques récurrents.
On te donne aussi le domaine déjà identifié.

Ta tâche : produire une fiche CIR claire et des mots-clés.

RÈGLES ABSOLUES :
1. Réponds UNIQUEMENT en JSON valide. Aucun texte avant ou après.
2. Tu ne dois utiliser QUE les informations de la synthèse fournie.
3. N'invente aucune preuve, aucun résultat, aucun chiffre.
4. Pour chaque élément, le champ "preuves" doit contenir des phrases copiées
   depuis la synthèse fournie.
5. Ne mets pas de titres seuls comme preuves.
6. Classe correctement :
   - "objectif" = but à atteindre.
   - "verrou" = limite, impossibilité, difficulté, risque, non-résolution.
   - "etat_art" = solutions existantes et leurs limites.
   - "demarche" = actions réalisées : conception, choix, définition, développement.
   - "essais" = tests, mesures, évaluations, prototypes soumis à essai.
   - "resultats" = constats obtenus, solution retenue, performance observée.
7. "mots_cles" : 6 à 15 concepts techniques centraux du projet.
8. "lacunes" : uniquement les lacunes réellement visibles.

FORMAT JSON STRICT :
{
  "objet_du_projet": {"resume": "...", "preuves": ["..."]},
  "objectifs": [{"resume": "...", "preuves": ["..."]}],
  "verrous": [{"resume": "...", "preuves": ["..."]}],
  "etat_art": [{"resume": "...", "preuves": ["..."]}],
  "demarche": [{"resume": "...", "preuves": ["..."]}],
  "essais": [{"resume": "...", "preuves": ["..."]}],
  "resultats": [{"resume": "...", "preuves": ["..."]}],
  "mots_cles": ["...", "..."],
  "lacunes": ["..."]
}
"""

USER_PROMPT_TEMPLATE = """# DOMAINE IDENTIFIÉ

{domaine}

# SYNTHÈSE DES PREUVES EXTRAITES DU DOSSIER

{evidence_summary}

# CONSIGNE
Rédige la fiche CIR et les mots-clés en t'appuyant UNIQUEMENT sur cette synthèse.
Les preuves citées doivent provenir de la synthèse ci-dessus.
Réponds uniquement avec le JSON strict.
"""


# ══════════════════════════════════════════════════════════════════════════════
# UTILITAIRES GÉNÉRAUX
# ══════════════════════════════════════════════════════════════════════════════

def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'").replace("`", "'")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


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

    variants = [
        raw,
        re.sub(r"```(?:json)?|```", "", raw, flags=re.I).strip(),
    ]

    for candidate in variants:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    return None


def _safe_get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _get_by_role(aggregated: Any) -> dict:
    by_role = _safe_get(aggregated, "by_role", {}) or {}
    return by_role if isinstance(by_role, dict) else {}


def _get_items_for_role(aggregated: Any, role: str) -> list[Any]:
    return list((_get_by_role(aggregated).get(role) or []))


def _get_phrase(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("phrase", "") or "").strip()
    return str(getattr(item, "phrase", "") or "").strip()


def _get_frequency(item: Any) -> int:
    if isinstance(item, dict):
        return int(item.get("frequency", 1) or 1)
    return int(getattr(item, "frequency", 1) or 1)


def _get_confidence(item: Any) -> float:
    if isinstance(item, dict):
        return float(item.get("confidence", 0.7) or 0.7)
    return float(getattr(item, "confidence", 0.7) or 0.7)


# ══════════════════════════════════════════════════════════════════════════════
# SCHÉMA STRUCTURÉ OLLAMA
# ══════════════════════════════════════════════════════════════════════════════

_FICHE_ELEMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "resume": {"type": "string"},
        "preuves": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["resume"],
}

_OLLAMA_SCHEMA = {
    "type": "object",
    "properties": {
        "objet_du_projet": _FICHE_ELEMENT_SCHEMA,
        "objectifs": {"type": "array", "items": _FICHE_ELEMENT_SCHEMA},
        "verrous": {"type": "array", "items": _FICHE_ELEMENT_SCHEMA},
        "etat_art": {"type": "array", "items": _FICHE_ELEMENT_SCHEMA},
        "demarche": {"type": "array", "items": _FICHE_ELEMENT_SCHEMA},
        "essais": {"type": "array", "items": _FICHE_ELEMENT_SCHEMA},
        "resultats": {"type": "array", "items": _FICHE_ELEMENT_SCHEMA},
        "mots_cles": {"type": "array", "items": {"type": "string"}},
        "lacunes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["objectifs", "verrous", "mots_cles"],
}


# ══════════════════════════════════════════════════════════════════════════════
# APPELS LLM
# ══════════════════════════════════════════════════════════════════════════════

def _call_ollama(domaine: str, evidence_summary: str, model: str, retry: int = 0) -> Optional[dict]:
    if ollama is None:
        logger.error("ollama non installé : pip install ollama")
        return None

    local_model = _clean_local_model_name(model)

    try:
        response = ollama.chat(
            model=local_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": USER_PROMPT_TEMPLATE.format(
                        domaine=domaine,
                        evidence_summary=evidence_summary,
                    ),
                },
            ],
            format=_OLLAMA_SCHEMA,
            options={
                "temperature": 0,
                "top_p": 0.1,
                "num_ctx": 16384,
                "num_predict": 1800,
            },
        )
        content = response.get("message", {}).get("content", "")
        data = _extract_json(content)

        if data is None and retry < MAX_RETRIES:
            time.sleep(1.2)
            return _call_ollama(domaine, evidence_summary, model, retry + 1)

        return data

    except Exception as exc:
        logger.exception("Erreur Ollama synthesizer (retry=%d) : %s", retry, exc)
        if retry < MAX_RETRIES:
            time.sleep(1.2)
            return _call_ollama(domaine, evidence_summary, model, retry + 1)
        return None


def _call_openrouter(domaine: str, evidence_summary: str, model: str) -> Optional[dict]:
    client = _get_openrouter_client()
    if client is None:
        return None

    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": USER_PROMPT_TEMPLATE.format(
                        domaine=domaine,
                        evidence_summary=evidence_summary,
                    ),
                },
            ],
            temperature=0,
            max_tokens=2500,
            timeout=TIMEOUT_SECONDS,
        )
        return _extract_json(completion.choices[0].message.content)

    except Exception as exc:
        logger.exception("Erreur OpenRouter synthesizer : %s", exc)
        return None


def _call_llm(domaine: str, evidence_summary: str, model: str) -> tuple[Optional[dict], str]:
    if _is_local_model(model):
        return _call_ollama(domaine, evidence_summary, model), "ollama"
    return _call_openrouter(domaine, evidence_summary, model), "openrouter"


# ══════════════════════════════════════════════════════════════════════════════
# VALIDATION / NETTOYAGE DES PREUVES
# ══════════════════════════════════════════════════════════════════════════════

BAD_STRUCTURAL_RE = re.compile(
    r"^(?:"
    r"objectifs?(?: vis[ée]s.*)?|"
    r"contexte(?: de l['’]op[ée]ration)?|"
    r"analyse de l['’]?[ée]tat de l['’]?art|"
    r"[ée]tat de l['’]?art(?: et verrous)?|"
    r"verrous?.*|"
    r"r[ée]sultats?(?: de R&D| obtenus)?|"
    r"annexes?|"
    r"description des ressources humaines|"
    r"op[ée]ration de R&D.*|"
    r"intitul[ée] de l['’]op[ée]ration|"
    r"raisonnement scientifique.*description des travaux.*"
    r")\s*\d{0,3}\s*$",
    re.I | re.U,
)

INTRO_ONLY_RE = re.compile(
    r"^(?:"
    r"les r[ée]sultats de ces essais .* sont\s*:|"
    r"les travaux de R&D d[ée]crivant cette partie sont pr[ée]sent[ée]s.*|"
    r"les coordonn[ée]es de la personne r[ée]f[ée]rente.*|"
    r"le tableau suivant.*|"
    r"comme illustr[ée] dans la figure.*"
    r")$",
    re.I | re.U,
)

ADMIN_OR_LOW_VALUE_RE = re.compile(
    r"(?:"
    r"nom pr[ée]nom|dipl[oô]me|fonction dans l['’]op[ée]ration|"
    r"adresse [ée]lectronique|num[ée]ro de t[ée]l[ée]phone|"
    r"co[uû]t total|co[uû]t d[ée]clar[ée]|rescrit|agr[ée]ment|"
    r"table des mati[èe]res|sommaire"
    r")",
    re.I | re.U,
)


def _is_bad_structural_phrase(phrase: str) -> bool:
    p = re.sub(r"\s+", " ", str(phrase or "")).strip()
    if not p:
        return True
    if len(p) < 25:
        return True
    if BAD_STRUCTURAL_RE.match(p):
        return True
    if INTRO_ONLY_RE.match(p):
        return True
    if re.search(r"table des mati[èe]res|sommaire", p, re.I):
        return True
    return False


def _is_allowed_phrase_for_role(phrase: str, role: str) -> bool:
    """
    Filtre léger post-LLM, universel.
    Version 2.0 : signaux élargis pour tous les domaines.
    """
    if _is_bad_structural_phrase(phrase):
        return False

    low = _norm(phrase)

    if ADMIN_OR_LOW_VALUE_RE.search(phrase):
        return False

    if role == "objectif":
        return bool(re.search(
            r"objectif|vise|afin de|d[ée]velopper|mettre en [oeœ]uvre|permettre|r[ée]pondre aux|"
            r"doit|nous devons|travaux .* afin de|créer|concevoir|améliorer|optimiser|"
            r"atteindre|réaliser|produire|démontrer|valider",
            low,
            re.I,
        ))

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

    if role == "etat_art":
        return bool(re.search(
            r"[ée]tat de l['’]?art|solutions? existantes?|avanc[ée]es technologiques|"
            r"sont utilis[ée]es|ont [ée]t[ée] d[ée]velopp[ée]es|processus complexe|"
            r"permettent de d[ée]velopper|choix populaire|"
            r"littérature|publications|travaux antérieurs|"
            r"comparé à|différent de|similaire à",
            low,
            re.I,
        ))

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

    if role == "essai":
        return bool(re.search(
            r"essai|test|[ée]valuer|[ée]valuation|mesur|validation|"
            r"prototypes?.*test|apr[èe]s introduction|nous avons remarqu[ée]|"
            r"nous avons constat[ée]|expérimentation|manipulation|"
            r"analyse de données|vérification expérimentale",
            low,
            re.I,
        ))

    if role == "resultat":
        return bool(re.search(
            r"r[ée]sultats?|a permis|ont permis|sommes parvenus|retenue|retenu|"
            r"positifs|satisfaisants|montre que|absence de|nous avons d[ée]velopp[ée]|"
            r"mise en [œoe]uvre|performances .* port[ée]|conclusion|"
            r"a démontré|a prouvé|a validé|a confirmé|"
            r"\d+(?:[,.]\d+)?\s*(?:%|°C|N|Pa|Hz|ms|s|mn|h)",
            low,
            re.I,
        ))

    return True


def _collect_known_phrases(aggregated: Any) -> set[str]:
    known: set[str] = set()
    by_role = _get_by_role(aggregated)
    for items in by_role.values():
        for item in items or []:
            phrase = _get_phrase(item)
            if phrase:
                known.add(_norm(phrase))
    return known


def _best_known_phrase_match(raw: str, known_phrases_norm: set[str], known_originals: dict[str, str]) -> Optional[str]:
    pn = _norm(raw)
    if not pn:
        return None

    if pn in known_originals:
        return known_originals[pn]

    best_key = None
    best_score = 0

    words = set(re.findall(r"[a-z0-9]{4,}", pn))
    for kp in known_phrases_norm:
        if len(kp) < 20:
            continue
        if pn in kp or kp in pn:
            return known_originals.get(kp, raw)

        kw = set(re.findall(r"[a-z0-9]{4,}", kp))
        score = len(words & kw)
        if score > best_score:
            best_score = score
            best_key = kp

    if best_key and best_score >= 5:
        return known_originals.get(best_key, raw)

    return None


def _parse_fiche_element(
    raw: Any,
    known_phrases_norm: set[str],
    known_originals: dict[str, str],
    role: str,
) -> Optional[FicheElement]:
    if not isinstance(raw, dict):
        return None

    resume = re.sub(r"\s+", " ", str(raw.get("resume", "") or "")).strip()
    if not resume:
        return None

    preuves: list[str] = []
    for p in raw.get("preuves", []) or []:
        p = re.sub(r"\s+", " ", str(p or "")).strip()
        if not p:
            continue

        matched = _best_known_phrase_match(p, known_phrases_norm, known_originals)
        if not matched:
            logger.debug("Preuve non rattachée aux preuves connues : %s", p[:80])
            continue

        if not _is_allowed_phrase_for_role(matched, role):
            continue

        if matched not in preuves:
            preuves.append(matched)

    if not preuves:
        return None

    if _is_bad_structural_phrase(resume):
        resume = _resume_from_phrase(preuves[0], role)

    return FicheElement(resume=resume, preuves=preuves)


def _parse_fiche_list(
    raw: Any,
    known_phrases_norm: set[str],
    known_originals: dict[str, str],
    role: str,
) -> list[FicheElement]:
    out: list[FicheElement] = []
    seen = set()

    for item in raw or []:
        el = _parse_fiche_element(item, known_phrases_norm, known_originals, role)
        if not el:
            continue
        key = _norm(el.resume + " " + " ".join(el.preuves))
        if key in seen:
            continue
        seen.add(key)
        out.append(el)

    return out


# ══════════════════════════════════════════════════════════════════════════════
# FALLBACK EXTRACTIF ET RECLASSEMENT
# ══════════════════════════════════════════════════════════════════════════════

def _rank_phrase_for_role(phrase: str, role: str) -> int:
    low = _norm(phrase)
    score = min(len(phrase) // 60, 8)

    role_signals = {
        "objectif": r"objectif|vise|afin de|d[ée]velopper|permettre|r[ée]pondre aux|doit|nous devons|créer|concevoir|améliorer",
        "verrou": r"verrou|incapacit|ne permet pas|ne pouvons pas|difficult|risque|probl[èe]matique|manque|toutefois|cependant|contrainte|blocage|défi",
        "etat_art": r"[ée]tat de l['’]?art|solutions? existantes?|avanc[ée]es technologiques|sont utilis[ée]es|ont [ée]t[ée] d[ée]velopp[ée]es",
        "demarche": r"nous avons|d[ée]fini|r[ée]alis[ée]|d[ée]velopp[ée]|travaux ont port[ée]|prototype|méthode|protocole|approche|stratégie|processus",
        "essai": r"essai|test|[ée]valuer|[ée]valuation|mesur|validation|expérimentation",
        "resultat": r"r[ée]sultats?|a permis|ont permis|sommes parvenus|retenue|retenu|positifs|satisfaisants|performances",
        "preuve": r"\d|%|°C|bar|min|par calcul|selon la norme|hauteur|distance|H1|H2",
    }

    pattern = role_signals.get(role)
    if pattern and re.search(pattern, low, re.I):
        score += 25

    if _is_bad_structural_phrase(phrase):
        score -= 80

    if len(phrase) >= 80:
        score += 5
    if re.search(r";|:|,|\. ", phrase):
        score += 2

    return score


def _resume_from_phrase(phrase: str, role: str) -> str:
    p = re.sub(r"\s+", " ", str(phrase or "")).strip()
    if len(p) <= 220:
        return p
    cut = p[:220].rsplit(" ", 1)[0]
    return cut + "..."


def _fallback_elements_from_aggregated(aggregated: Any, role: str, max_items: int = 3) -> list[FicheElement]:
    candidates: list[tuple[int, str]] = []

    for item in _get_items_for_role(aggregated, role):
        phrase = _get_phrase(item)
        if not phrase:
            continue
        if not _is_allowed_phrase_for_role(phrase, role):
            continue
        candidates.append((_rank_phrase_for_role(phrase, role), phrase))

    candidates.sort(key=lambda x: (-x[0], len(x[1])))

    out: list[FicheElement] = []
    seen = set()

    for _, phrase in candidates:
        key = _norm(phrase)
        if key in seen:
            continue
        seen.add(key)
        out.append(FicheElement(resume=_resume_from_phrase(phrase, role), preuves=[phrase]))
        if len(out) >= max_items:
            break

    return out


def _move_misclassified_elements(result: Synthesis) -> None:
    """
    Corrige les erreurs de classement évidentes après LLM + fallback.
    """
    new_demarche: list[FicheElement] = []
    new_essais: list[FicheElement] = list(result.essais)
    new_resultats: list[FicheElement] = list(result.resultats)
    new_verrous: list[FicheElement] = list(result.verrous)

    def add_unique(target: list[FicheElement], el: FicheElement) -> None:
        key = _norm(el.resume + " " + " ".join(el.preuves))
        for existing in target:
            if _norm(existing.resume + " " + " ".join(existing.preuves)) == key:
                return
        target.append(el)

    for el in result.demarche:
        text = " ".join([el.resume] + el.preuves)
        low = _norm(text)

        if _is_allowed_phrase_for_role(text, "essai") and re.search(r"essai|test|expérimentation|mesure", low):
            add_unique(new_essais, el)
            continue

        if _is_allowed_phrase_for_role(text, "resultat") and re.search(r"r[ée]sultat|ont permis|a permis|positifs|performances", low):
            add_unique(new_resultats, el)
            continue

        if _is_allowed_phrase_for_role(text, "verrou") and re.search(r"ne permet pas|difficult|risque|incapacit|contrainte|blocage", low):
            add_unique(new_verrous, el)
            continue

        if _is_allowed_phrase_for_role(text, "demarche"):
            new_demarche.append(el)

    result.demarche = _dedup_elements(new_demarche)
    result.essais = _dedup_elements(new_essais)
    result.resultats = _dedup_elements(new_resultats)
    result.verrous = _dedup_elements(new_verrous)


def _dedup_elements(elements: list[FicheElement]) -> list[FicheElement]:
    out: list[FicheElement] = []
    seen: set[str] = set()

    for el in elements:
        if not el or not el.preuves:
            continue
        key = _norm(" ".join(el.preuves))
        if key in seen:
            continue
        seen.add(key)
        out.append(el)

    return out


# ══════════════════════════════════════════════════════════════════════════════
# FORCER L'INCLUSION DE TOUTES LES PREUVES (CORRIGÉ - VERSION AVEC MAPPING)
# ══════════════════════════════════════════════════════════════════════════════

def _force_all_evidences_for_role(synthesis: Synthesis, aggregated: Any, role: str, max_items: int = 10) -> None:
    """
    Force l'inclusion des preuves d'un rôle dans la fiche CIR.
    UNIVERSEL : fonctionne pour verrous, objectifs, demarche, resultats, etc.
    
    CORRECTION : utilise un dictionnaire de mapping car certains attributs
    n'ont pas le même nom au pluriel (ex: demarche reste demarche).
    """
    # Mapping entre le nom du rôle (utilisé dans aggregated) et le nom de l'attribut dans Synthesis
    attribute_map = {
        "verrou": "verrous",
        "objectif": "objectifs",
        "demarche": "demarche",      # ← Attention: singulier, pas de 's' !
        "resultat": "resultats",
        "etat_art": "etat_art",
        "essai": "essais",
        "preuve": "preuves",
        "contexte": "contexte",
    }
    
    attr_name = attribute_map.get(role, f"{role}s")
    
    existing_phrases = set()
    current_list = getattr(synthesis, attr_name, [])
    for item in current_list:
        for p in getattr(item, "preuves", []):
            existing_phrases.add(_norm(p))
    
    added = 0
    for item in _get_items_for_role(aggregated, role):
        if added >= max_items:
            break
        phrase = _get_phrase(item)
        if not phrase or _norm(phrase) in existing_phrases:
            continue
        
        new_element = FicheElement(
            resume=phrase[:200] + "..." if len(phrase) > 200 else phrase,
            preuves=[phrase]
        )
        getattr(synthesis, attr_name).append(new_element)
        added += 1
    
    if added:
        logger.info("Forcé l'ajout de %d éléments manquants pour le rôle '%s' (attribut: %s)", added, role, attr_name)


# ══════════════════════════════════════════════════════════════════════════════
# MOTS-CLÉS / LACUNES
# ══════════════════════════════════════════════════════════════════════════════

def _is_admin_keyword(text: str) -> bool:
    txt = str(text or "").strip()

    if not txt:
        return True

    if len(txt) < 3:
        return True

    if re.search(
        r"^(CIR|R&D|202[0-9]|co[uû]t|date de|nom pr[ée]nom|travaux de R&D|"
        r"technique|technologique|technologiques|conclusion|intitul[ée]|"
        r"attentes du march[ée]|organisation|plan strat[ée]gique)$",
        txt,
        re.I,
    ):
        return True

    if re.search(r"@\w+|t[ée]l[ée]phone|adresse|interlocuteur", txt, re.I):
        return True

    return False


def _keyword_score(text: str, frequency: int = 1) -> int:
    low = _norm(text)
    score = int(frequency) * 10

    if len(text) >= 8:
        score += 2
    if len(text.split()) >= 2:
        score += 4

    if re.search(
        r"(?:prototype|test|essai|syst[èe]me|méthode|algorithme|modèle|"
        r"matériau|composant|procédé|technique|innovation|brevet|"
        r"performance|qualité|couverture|compilabilité|LLM|IA|R&D)",
        text,
        re.I,
    ):
        score += 12

    if _is_admin_keyword(text):
        score -= 100

    return score


def _keywords_from_aggregated(aggregated: Any, max_keywords: int = 15) -> list[str]:
    concepts = _safe_get(aggregated, "concepts", []) or []
    scored: list[tuple[int, str]] = []

    for c in concepts:
        if isinstance(c, dict):
            txt = c.get("text")
            freq = c.get("frequency", 1)
        else:
            txt = getattr(c, "text", None)
            freq = getattr(c, "frequency", 1)

        if not txt:
            continue

        txt = re.sub(r"\s+", " ", str(txt)).strip()
        if _is_admin_keyword(txt):
            continue

        scored.append((_keyword_score(txt, int(freq or 1)), txt))

    scored.sort(key=lambda x: (-x[0], len(x[1])))

    out: list[str] = []
    seen: set[str] = set()

    for score, txt in scored:
        if score <= 0:
            continue
        key = _norm(txt)
        if key in seen:
            continue
        seen.add(key)
        out.append(txt)
        if len(out) >= max_keywords:
            break

    return out


def _clean_keywords(keywords: list[str], aggregated: Any, max_keywords: int = 15) -> list[str]:
    candidates: list[tuple[int, str]] = []

    for kw in keywords or []:
        kw = re.sub(r"\s+", " ", str(kw or "")).strip()
        if not kw or _is_admin_keyword(kw):
            continue
        candidates.append((_keyword_score(kw, 1), kw))

    for kw in _keywords_from_aggregated(aggregated, max_keywords=30):
        candidates.append((_keyword_score(kw, 2), kw))

    candidates.sort(key=lambda x: (-x[0], len(x[1])))

    out: list[str] = []
    seen = set()

    for score, kw in candidates:
        if score <= 0:
            continue
        k = _norm(kw)
        if k in seen:
            continue
        seen.add(k)
        out.append(kw)
        if len(out) >= max_keywords:
            break

    return out


def _has_numeric_results(aggregated: Any) -> bool:
    for role in ("resultat", "essai", "preuve"):
        for item in _get_items_for_role(aggregated, role):
            phrase = _get_phrase(item)
            if re.search(r"\d+(?:[,.]\d+)?\s*(?:%|°C|bar|min|mm|cm|kg|N|MPa|KiloGrays|fois|ms|s|Hz|W|V|A|Ω|€)", phrase, re.I):
                return True
    return False


def _clean_lacunes(lacunes: list[str], aggregated: Any) -> list[str]:
    by_role = _get_by_role(aggregated)
    counts = {role: len(items or []) for role, items in by_role.items()}

    has_numeric = _has_numeric_results(aggregated)
    cleaned: list[str] = []

    for lac in lacunes or []:
        l = re.sub(r"\s+", " ", str(lac or "")).strip()
        if not l:
            continue

        low = _norm(l)

        if "verrou" in low and counts.get("verrou", 0) >= 2:
            continue

        if ("essai" in low or "test" in low) and counts.get("essai", 0) >= 1:
            continue

        if ("resultat" in low or "résultat" in l.lower()) and counts.get("resultat", 0) >= 1:
            if "chiffr" in low or "quantifi" in low:
                if has_numeric:
                    cleaned.append("Résultats globalement présents, mais peu de métriques finales consolidées.")
                else:
                    cleaned.append("Résultats présents, mais principalement qualitatifs et peu chiffrés.")
            continue

        cleaned.append(l)

    if not cleaned and counts.get("resultat", 0) >= 1 and not has_numeric:
        cleaned.append("Résultats présents, mais principalement qualitatifs et peu chiffrés.")

    out: list[str] = []
    seen = set()
    for l in cleaned:
        k = _norm(l)
        if k and k not in seen:
            seen.add(k)
            out.append(l)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ══════════════════════════════════════════════════════════════════════════════

def synthesize(
    aggregated: Any,
    domain_classification: Any = None,
    model: str = DEFAULT_LLM_MODEL,
    enabled: bool = True,
) -> Synthesis:
    """
    Produit la fiche CIR finale + les mots-clés.

    Parameters
    ----------
    aggregated:
        Résultat de aggregator.aggregate().
    domain_classification:
        Résultat de domain_classifier.classify_domain().
    model:
        Modèle LLM, ex: "ollama:qwen2.5:7b-instruct".
    enabled:
        Si False, retourne une synthèse vide.
    """
    result = Synthesis()
    result.model = model
    t0 = time.time()

    if not enabled:
        result.processing_time = time.time() - t0
        return result

    # Domaine.
    if domain_classification is not None:
        result.domaine_principal = _safe_get(domain_classification, "domaine_principal", "non_classifié")
        if hasattr(domain_classification, "to_dict"):
            result.domaine_detail = domain_classification.to_dict()
        elif isinstance(domain_classification, dict):
            result.domaine_detail = domain_classification

    # Synthèse des preuves.
    if hasattr(aggregated, "summary_for_llm"):
        evidence_summary = aggregated.summary_for_llm()
    else:
        evidence_summary = str(aggregated or "")

    if not evidence_summary.strip():
        result.errors.append("Aucune preuve à synthétiser.")
        result.processing_time = time.time() - t0
        return result

    known_originals: dict[str, str] = {}
    by_role = _get_by_role(aggregated)
    for items in by_role.values():
        for item in items or []:
            phrase = _get_phrase(item)
            if phrase:
                known_originals[_norm(phrase)] = phrase

    known_phrases = set(known_originals)

    data, backend = _call_llm(result.domaine_principal, evidence_summary, model)
    result.llm_calls = 1
    result.backend = backend

    if data is None:
        result.errors.append("Aucune réponse LLM exploitable pour la synthèse. Fallback extractif utilisé.")
        _fill_from_fallback(result, aggregated)
        result.processing_time = time.time() - t0
        return result

    try:
        # Objet du projet.
        obj_raw = data.get("objet_du_projet")
        if obj_raw:
            result.objet_du_projet = _parse_fiche_element(
                obj_raw, known_phrases, known_originals, "objectif"
            )

        # Listes.
        result.objectifs = _parse_fiche_list(data.get("objectifs"), known_phrases, known_originals, "objectif")
        result.verrous = _parse_fiche_list(data.get("verrous"), known_phrases, known_originals, "verrou")
        result.etat_art = _parse_fiche_list(data.get("etat_art"), known_phrases, known_originals, "etat_art")
        result.demarche = _parse_fiche_list(data.get("demarche"), known_phrases, known_originals, "demarche")
        result.essais = _parse_fiche_list(data.get("essais"), known_phrases, known_originals, "essai")
        result.resultats = _parse_fiche_list(data.get("resultats"), known_phrases, known_originals, "resultat")

        # Fallbacks par rôle.
        if result.objet_du_projet is None:
            obj_candidates = (
                _fallback_elements_from_aggregated(aggregated, "objectif", 1)
                or _fallback_elements_from_aggregated(aggregated, "contexte", 1)
            )
            if obj_candidates:
                result.objet_du_projet = obj_candidates[0]

        if not result.objectifs:
            result.objectifs = _fallback_elements_from_aggregated(aggregated, "objectif", 8)
        if not result.verrous:
            result.verrous = _fallback_elements_from_aggregated(aggregated, "verrou", 10)
        if not result.etat_art:
            result.etat_art = _fallback_elements_from_aggregated(aggregated, "etat_art", 6)
        if not result.demarche:
            result.demarche = _fallback_elements_from_aggregated(aggregated, "demarche", 8)
        if not result.essais:
            result.essais = _fallback_elements_from_aggregated(aggregated, "essai", 8)
        if not result.resultats:
            result.resultats = _fallback_elements_from_aggregated(aggregated, "resultat", 8)

        _move_misclassified_elements(result)

        # FORCER L'INCLUSION DE TOUTES LES PREUVES (avec mapping corrigé)
        _force_all_evidences_for_role(result, aggregated, "verrou", max_items=12)
        _force_all_evidences_for_role(result, aggregated, "objectif", max_items=12)
        _force_all_evidences_for_role(result, aggregated, "demarche", max_items=8)
        _force_all_evidences_for_role(result, aggregated, "resultat", max_items=8)
        _force_all_evidences_for_role(result, aggregated, "etat_art", max_items=8)
        _force_all_evidences_for_role(result, aggregated, "essai", max_items=8)

        # Déduplication finale
        result.objectifs = _dedup_elements(result.objectifs)[:12]
        result.verrous = _dedup_elements(result.verrous)[:12]
        result.etat_art = _dedup_elements(result.etat_art)[:10]
        result.demarche = _dedup_elements(result.demarche)[:10]
        result.essais = _dedup_elements(result.essais)[:10]
        result.resultats = _dedup_elements(result.resultats)[:10]

        result.mots_cles = _clean_keywords(data.get("mots_cles", []) or [], aggregated, max_keywords=20)
        if not result.mots_cles:
            result.mots_cles = _keywords_from_aggregated(aggregated, 20)

        result.lacunes = _clean_lacunes(data.get("lacunes", []) or [], aggregated)

    except Exception as exc:
        logger.exception("Erreur parsing synthèse : %s", exc)
        result.errors.append(f"Parsing synthèse : {type(exc).__name__}: {exc}")
        _fill_from_fallback(result, aggregated)

    result.processing_time = time.time() - t0

    logger.info(
        "Synthèse [%s:%s] : %d objectifs, %d verrous, %d démarches, %d essais, %d résultats, %d mots-clés | domaine=%s",
        result.backend,
        result.model,
        len(result.objectifs),
        len(result.verrous),
        len(result.demarche),
        len(result.essais),
        len(result.resultats),
        len(result.mots_cles),
        result.domaine_principal,
    )

    return result


def _fill_from_fallback(result: Synthesis, aggregated: Any) -> None:
    result.objet_du_projet = (_fallback_elements_from_aggregated(aggregated, "objectif", 1) or [None])[0]
    result.objectifs = _fallback_elements_from_aggregated(aggregated, "objectif", 8)
    result.verrous = _fallback_elements_from_aggregated(aggregated, "verrou", 10)
    result.etat_art = _fallback_elements_from_aggregated(aggregated, "etat_art", 6)
    result.demarche = _fallback_elements_from_aggregated(aggregated, "demarche", 8)
    result.essais = _fallback_elements_from_aggregated(aggregated, "essai", 8)
    result.resultats = _fallback_elements_from_aggregated(aggregated, "resultat", 8)
    _move_misclassified_elements(result)
    
    # Forcer aussi dans le fallback
    _force_all_evidences_for_role(result, aggregated, "verrou", max_items=12)
    _force_all_evidences_for_role(result, aggregated, "objectif", max_items=12)
    _force_all_evidences_for_role(result, aggregated, "demarche", max_items=8)
    _force_all_evidences_for_role(result, aggregated, "resultat", max_items=8)
    
    result.mots_cles = _keywords_from_aggregated(aggregated, 20)
    result.lacunes = _clean_lacunes([], aggregated)


# ── Debug local ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO)

    print("synthesizer.py OK - version universelle 1.3.1 (corrigée)")
    print("Modèle par défaut :", DEFAULT_LLM_MODEL)