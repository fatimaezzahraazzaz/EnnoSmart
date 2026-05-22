"""
modules/NLP/evidence_mapper.py — LE CŒUR DU PIPELINE
──────────────────────────────────────────────────────────────────────────────
Approche evidence-first : on ne classe plus des MOTS, on classe la FONCTION
ARGUMENTATIVE des PASSAGES.

  segmenter.py ──► evidence_mapper.py ──► aggregator.py

CORRECTIONS v2.1.0 :
  1. MAX_LLM_PASSAGES : 14 → 22  (plus de passages traités par document)
  2. Passages de type "table" : chemin dédié d'extraction d'entités
     structurées (personnes, équipements, partenaires) sans scoring R&D.
     Ces passages ne comptent pas dans le budget LLM principal.
  3. Prompt dédié pour les tableaux (extraction NOM / RÔLE / VALEUR).
  4. Le scoring écarte toujours les tableaux admin du budget R&D mais
     les route vers _extract_table_entities().

Version : 2.1.0-universal
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

DEFAULT_LLM_MODEL = "ollama:qwen2.5:7b-instruct"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
LOCAL_MODEL_PREFIXES = ("ollama:", "local:")

MAX_PASSAGE_CHARS = 3200
MAX_RETRIES = 1
TIMEOUT_SECONDS = 90

VALID_ROLES = {
    "contexte",
    "objectif",
    "verrou",
    "etat_art",
    "demarche",
    "essai",
    "resultat",
    "preuve",
    "metrique",
    "administratif",
    "hors_sujet",
}

DIAGNOSTIC_ROLES = {
    "contexte", "objectif", "verrou", "etat_art",
    "demarche", "essai", "resultat", "preuve", "metrique",
}

# CORRIGÉ : était 14, maintenant 22 pour couvrir les documents longs
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

# ── Signaux spécifiques aux tableaux d'entités ───────────────────────────────
# Un tableau contient des entités nommées si on trouve ces en-têtes de colonnes
TABLE_ENTITY_SIGNAL_RE = re.compile(
    r"(?:nom\s+pr[ée]nom|pr[ée]nom\s+nom|nom\s+de\s+l['']?(?:organisme|partenaire|entreprise)|"
    r"responsable|chef de projet|ing[ée]nieur|technicien|diplôme|"
    r"temps d[ée]clar[ée]|heures|etp|partenaire|sous-traitant|co-?traitant|"
    r"fournisseur|équipement|instrument|logiciel|outil)",
    re.I | re.U,
)

_ARTIFACT_PHRASE_RE = re.compile(
    r"^(?:"
    r"[a-z]\s+"
    r"|(?:et|ou|mais|donc|car|ni|or|que|qui|dont|où|si|bien|ainsi)\s"
    r"|(?:de|du|des|le|la|les|un|une|en|au|aux)\s[a-z]"
    r")",
    re.UNICODE,
)

_ADMIN_CIR_JUSTIF_RE = re.compile(
    r"(manuel de frascati|crit[eè]res? d[''']?[ée]ligibilit[ée]|"
    r"s[ée]lectionn[ée]\s+en\s+se\s+basant|crit[eè]res?\s+suivants?|"
    r"d[ée]crit\s+dans\s+le\s+tableau|rescrit\s+CIR|agr[ée]ment\s+CIR|"
    r"OCDE|Organisation\s+de\s+Coop[ée]ration)",
    re.I | re.U,
)

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
    # NOUVEAU : tableaux d'entités → chemin séparé, pas scorés ici
    source_type = str(_passage_get(passage, "source_type", "text") or "text")
    if source_type == "table":
        return -998   # valeur sentinelle spéciale, ≠ -999 (qui = hors_sujet)

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

    # PATCH v2.5 : bonus pour les passages "unknown" très signalés.
    # Typique des documents narratifs sans structure de titres.
    if section_role == "unknown" and signal_count >= 3:
        score += 30  # Passe de ~62 à ~92 — dans la plage utile R&D

    return score


def _select_passages_for_llm(
    passages: list[Any],
    max_passages: int = MAX_LLM_PASSAGES,
) -> tuple[list[Any], list[Any], list["PassageMapping"]]:
    """
    Retourne :
      - selected       : passages R&D pour le LLM (budget max_passages)
      - table_passages : passages "table" pour extraction d'entités séparée
      - skipped_mappings : passages ignorés (hors_sujet, TdM…)
    """
    scored: list[tuple[int, int, Any]] = []
    table_passages: list[Any] = []
    skipped_mappings: list[PassageMapping] = []

    for i, p in enumerate(passages or []):
        text = str(_passage_get(p, "text", "") or "")
        passage_id = str(_passage_get(p, "passage_id", "") or f"p{i}")
        section_role = str(_passage_get(p, "section_role", "unknown") or "unknown")
        score = _score_passage_for_llm(p)

        if score == -998:
            # Tableau : chemin dédié
            table_passages.append(p)
            continue

        if score <= -999:
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

    return selected, table_passages, skipped_mappings


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
    # NOUVEAU : entités structurées extraites des tableaux
    structured_entities: dict = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "passage_id": self.passage_id,
            "roles_cir": self.roles_cir,
            "evidences": [e.to_dict() for e in self.evidences],
            "concepts": self.concepts,
            "error": self.error,
        }
        if self.structured_entities:
            d["structured_entities"] = self.structured_entities
        return d


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

    def all_structured_entities(self) -> dict:
        """Agrège toutes les entités structurées extraites des tableaux."""
        result: dict[str, list] = {
            "personnes": [],
            "equipements": [],
            "partenaires_rd": [],
            "organismes": [],
        }
        seen: dict[str, set] = {k: set() for k in result}
        for m in self.mappings:
            for key, values in m.structured_entities.items():
                if key in result:
                    for v in values or []:
                        v_str = str(v or "").strip()
                        if v_str and v_str.lower() not in seen[key]:
                            seen[key].add(v_str.lower())
                            result[key].append(v_str)
        return result

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
# PROMPTS
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
Pour "objectif", cherche aussi les items de liste (tirets, puces) qui expriment un but à atteindre.
Pour "demarche", cherche les verbes d'action au passé (nous avons, a été réalisé, etc.).
Pour "verrou", cherche les expressions de blocage ou difficulté (ne permet pas, incapacité, manque...).
Pour "metrique", cherche les chiffres avec unités.
IMPORTANT : les items de listes à puces (- xxx ; - yyy) sont des phrases valides, extrais-les.
Réponds uniquement avec le JSON strict.
"""

# NOUVEAU : prompt dédié pour extraire les entités d'un tableau structuré
SYSTEM_PROMPT_TABLE = """Tu analyses un TABLEAU extrait d'un document de projet R&D.

Ta seule tâche : extraire les entités nommées structurées du tableau.

RÈGLES ABSOLUES :
1. Réponds UNIQUEMENT en JSON valide. Aucun texte avant ou après.
2. Recopie les valeurs telles qu'elles apparaissent dans le tableau.
3. N'invente rien. Si une catégorie est absente du tableau, laisse sa liste vide.

FORMAT JSON ATTENDU :
{
  "personnes": ["Prénom NOM (diplôme, fonction)", ...],
  "equipements": ["nom de l'équipement ou outil", ...],
  "partenaires_rd": ["nom du partenaire ou organisme", ...],
  "organismes": ["nom d'organisme ou d'institution", ...],
  "autres": ["toute autre entité notable", ...]
}

Pour "personnes" : inclus le nom complet, diplôme et fonction si disponibles.
Pour "equipements" : logiciels, appareils, instruments de mesure, bancs d'essai.
Pour "partenaires_rd" : laboratoires, universités, entreprises collaboratrices.
"""

USER_PROMPT_TABLE = """[type: tableau_structuré | section: {section_title}]

# TABLEAU À ANALYSER

{text}

Extrais les entités nommées (personnes, équipements, partenaires, organismes).
Recopie les valeurs exactes.
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

_OLLAMA_TABLE_SCHEMA = {
    "type": "object",
    "properties": {
        "personnes": {"type": "array", "items": {"type": "string"}},
        "equipements": {"type": "array", "items": {"type": "string"}},
        "partenaires_rd": {"type": "array", "items": {"type": "string"}},
        "organismes": {"type": "array", "items": {"type": "string"}},
        "autres": {"type": "array", "items": {"type": "string"}},
    },
}


def _call_llm(text: str, hint: str, model: str) -> tuple[Optional[dict], str]:
    """Appel LLM pour l'evidence mapping (passage R&D)."""
    user_msg = USER_PROMPT_TEMPLATE.format(hint=hint or "", text=text)
    return _call_llm_raw(SYSTEM_PROMPT, user_msg, model, _OLLAMA_SCHEMA)


def _call_llm_table(text: str, section_title: str, model: str) -> tuple[Optional[dict], str]:
    """Appel LLM dédié pour l'extraction d'entités d'un tableau."""
    user_msg = USER_PROMPT_TABLE.format(
        section_title=section_title or "inconnue",
        text=text,
    )
    return _call_llm_raw(SYSTEM_PROMPT_TABLE, user_msg, model, _OLLAMA_TABLE_SCHEMA)


def _call_llm_raw(
    system_prompt: str,
    user_msg: str,
    model: str,
    schema: dict,
) -> tuple[Optional[dict], str]:
    """Appel LLM générique avec fallback ollama → openrouter."""
    if _is_local_model(model) and ollama is not None:
        clean_model = _clean_local_model_name(model)
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = ollama.chat(
                    model=clean_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    format=schema,
                    options={"temperature": 0, "num_predict": 1200},
                )
                content = resp.message.content if hasattr(resp, "message") else str(resp)
                data = _extract_json(content)
                if data is not None:
                    return data, f"ollama:{clean_model}"
            except Exception as exc:
                if attempt < MAX_RETRIES:
                    time.sleep(1.5)
                else:
                    logger.warning("Ollama échec [%s] : %s", clean_model, exc)

    client = _get_openrouter_client()
    if client is not None:
        or_model = model if not _is_local_model(model) else "qwen/qwen-2.5-7b-instruct"
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = client.chat.completions.create(
                    model=or_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0,
                    max_tokens=1200,
                    timeout=TIMEOUT_SECONDS,
                )
                content = resp.choices[0].message.content if resp.choices else ""
                data = _extract_json(content)
                if data is not None:
                    return data, f"openrouter:{or_model}"
            except Exception as exc:
                if attempt < MAX_RETRIES:
                    time.sleep(2)
                else:
                    logger.warning("OpenRouter échec [%s] : %s", or_model, exc)

    return None, "unavailable"


# ══════════════════════════════════════════════════════════════════════════════
# EXTRACTION D'ENTITÉS STRUCTURÉES (TABLEAUX)
# ══════════════════════════════════════════════════════════════════════════════

def _extract_table_entities(passage: Any, model: str) -> PassageMapping:
    """
    Traite un passage de type "table" pour extraire les entités nommées
    (personnes, équipements, partenaires…) via un prompt dédié.
    Ne compte pas dans le budget MAX_LLM_PASSAGES.
    """
    text = str(_passage_get(passage, "text", "") or "").strip()
    passage_id = str(_passage_get(passage, "passage_id", "") or "table_p")
    section_title = str(_passage_get(passage, "section_title", "") or "")

    mapping = PassageMapping(passage_id=passage_id, roles_cir=["administratif"])

    if not text or not TABLE_ENTITY_SIGNAL_RE.search(text):
        # Tableau sans entités intéressantes (ex: tableau de résultats numériques)
        mapping.roles_cir = ["hors_sujet"]
        return mapping

    data, _backend = _call_llm_table(text, section_title, model)

    if data is None:
        # Fallback extractif : regex sur les lignes du tableau
        mapping.structured_entities = _extract_table_entities_regex(text)
        return mapping

    entities: dict[str, list] = {}
    for key in ("personnes", "equipements", "partenaires_rd", "organismes", "autres"):
        values = data.get(key, [])
        if isinstance(values, list):
            cleaned = [str(v).strip() for v in values if str(v or "").strip()]
            if cleaned:
                entities[key] = cleaned

    mapping.structured_entities = entities
    if entities:
        mapping.roles_cir = ["administratif"]

    return mapping


def _extract_table_entities_regex(text: str) -> dict[str, list]:
    """
    Fallback sans LLM : extrait les noms de personnes depuis les lignes
    d'un tableau markdown (colonne NOM | Diplôme | Fonction…).
    """
    personnes: list[str] = []
    equipements: list[str] = []

    # Regex tableau markdown : | NOM PRENOM | ... | ... |
    # On cherche les lignes qui ont une cellule ressemblant à un nom propre
    for line in text.splitlines():
        if not re.match(r"^\s*\|", line):
            continue
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if not cells or re.match(r"^[-:]+$", cells[0]):
            continue  # Ligne séparatrice
        # Première cellule en MAJUSCULES ou Prénom NOM → personne
        if cells and re.match(r"^[A-ZÀÂÉÈÊËÏÎÔÙÛÜ][A-ZÀÂÉÈÊËÏÎÔÙÛÜA-Za-z\s'\-]{3,40}$", cells[0]):
            # Construire "NOM Prénom (diplôme, fonction)" si colonnes disponibles
            entry = cells[0]
            if len(cells) > 2:
                entry += f" ({', '.join(cells[1:3])})"
            personnes.append(entry)
        # Cellule contenant un outil/logiciel reconnaissable
        for cell in cells:
            if re.search(r"(?:TestLab|Matlab|Python|ANSYS|Abaqus|Catia|SolidWorks|LabVIEW|Arduino|Excel|COMSOL)", cell, re.I):
                equipements.append(cell)

    result: dict[str, list] = {}
    if personnes:
        result["personnes"] = personnes
    if equipements:
        result["equipements"] = equipements
    return result


# ══════════════════════════════════════════════════════════════════════════════
# PARSING DES RÉPONSES LLM (PASSAGES R&D)
# ══════════════════════════════════════════════════════════════════════════════

def _is_artifact_phrase(phrase: str) -> bool:
    p = re.sub(r"\s+", " ", str(phrase or "")).strip()
    if not p or len(p) < 25:
        return True
    if _ARTIFACT_PHRASE_RE.match(p):
        return True
    if re.search(r"table des mati[èe]res|sommaire", p, re.I):
        return True
    return False


def _is_false_verrou(phrase: str) -> bool:
    low = _norm_for_match(phrase)
    if _ADMIN_CIR_JUSTIF_RE.search(phrase):
        return True
    if _SPEC_NOT_VERROU_RE.search(phrase):
        return True
    return False


def _is_allowed_phrase_for_role(phrase: str, role: str) -> bool:
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
    """
    Filtre les phrases structurelles non-informatives.
    CORRIGÉ v2.1 : détecte les entrées de TdM du type "Titre ... 15"
    (texte court + numéro de page final, sans verbe conjugué).
    """
    p = re.sub(r"\s+", " ", str(phrase or "")).strip()
    if not p:
        return True
    if len(p) < 25:
        return True
    if re.search(r"table des mati[èe]res|sommaire", p, re.I):
        return True
    # Entrée de TdM : court + finit par un chiffre (numéro de page)
    # "Matériaux des joints d'étanchéité 15"
    # "Description des travaux réalisés l'année 2024 8"
    if re.match(r".{10,120}\s+\d{1,3}\s*$", p) and len(p.split()) <= 14:
        if not re.search(
            r"\b(?:est|sont|était|avons|avez|ont|sera|serait|permet"
            r"|nécessite|implique|consiste|vise|visant|porte|réalis)\b",
            p, re.I
        ):
            return True
    return False


def _parse_passage_response(
    data: dict,
    passage_text: str,
    passage_id: str,
    section_role: str,
) -> PassageMapping:
    mapping = PassageMapping(passage_id=passage_id)

    raw_roles = data.get("roles_cir", []) or []
    roles = []
    for r in raw_roles:
        r = str(r or "").strip().lower()
        if r in VALID_ROLES:
            roles.append(r)
    mapping.roles_cir = list(dict.fromkeys(roles)) or ["hors_sujet"]

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

        if _is_artifact_phrase(phrase):
            continue

        if role == "verrou" and _is_false_verrou(phrase):
            if _ADMIN_CIR_JUSTIF_RE.search(phrase):
                role = "administratif"
            elif _SPEC_NOT_VERROU_RE.search(phrase):
                role = "demarche"

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
    max_llm_passages: int | None = None,
) -> EvidenceMapResult:
    """
    Mappe chaque passage vers ses rôles CIR + preuves.

    - Les passages de type "table" sont traités séparément via
      _extract_table_entities() et ne consomment pas le budget LLM R&D.
    - Le budget principal est configurable via max_llm_passages.
    """
    result = EvidenceMapResult()
    result.model = model
    t0 = time.time()

    if not enabled or not passages:
        result.processing_time = time.time() - t0
        return result

    backend_used = "unknown"

    selected_passages, table_passages, skipped_mappings = _select_passages_for_llm(
        passages,
        max_llm_passages or MAX_LLM_PASSAGES
    )
    result.mappings.extend(skipped_mappings)

    # ── Traitement des passages R&D (LLM principal) ──────────────────────────
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

    # ── Traitement des tableaux (LLM dédié, hors budget) ─────────────────────
    for passage in table_passages:
        passage_id = str(getattr(passage, "passage_id", "") or "table_p")
        try:
            mapping = _extract_table_entities(passage, model)
            result.mappings.append(mapping)
        except Exception as exc:
            logger.exception("Erreur extraction tableau %s : %s", passage_id, exc)
            result.mappings.append(PassageMapping(passage_id=passage_id, error=str(exc)))

    result.backend = backend_used
    result.processing_time = time.time() - t0

    logger.info(
        "Evidence mapping [%s:%s] : %d passages | %d appels LLM R&D | %d tableaux | %d preuves | %d erreurs",
        result.backend, result.model,
        len(result.mappings), result.llm_calls,
        len(table_passages),
        len(result.all_evidences()), len(result.errors),
    )
    return result

# ══════════════════════════════════════════════════════════════════════════════
# PATCH UNIVERSEL v2.2 — garde-fous post-LLM
# Objectif : corriger les résidus observés sans règle liée à un domaine précis.
# - supprime les lignes de table des matières / titres structurels
# - promeut les infinitifs en objectifs quand la section est "objectifs"
# - reclasse la littérature vers etat_art au lieu de résultat/démarche/verrou
# - évite les fragments coupés au début de chunk
# ══════════════════════════════════════════════════════════════════════════════

_TOC_LINE_UNIVERSAL_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?"
    r"[A-Za-zÀ-ÿ0-9 ,;:'’()/_\-]{3,140}"
    r"\s+\.{0,}\s*\d{1,3}\s*$",
    re.I | re.U,
)

_CONJUGATED_VERB_SIGNAL_RE = re.compile(
    r"\b(?:est|sont|était|etaient|étaient|avons|avez|ont|sera|serait|permet|"
    r"permettent|nécessite|implique|consiste|vise|visant|porte|réalise|"
    r"réalisé|réalisée|réalisés|montr|révèl|indiqu|confirm|démontr|"
    r"développ|défini|identifi|calcul|mesur|compar)\b",
    re.I | re.U,
)

_OBJECTIVE_INFINITIVE_RE = re.compile(
    r"^\s*(?:[-•*]\s*)?(?:"
    r"mener|définir|definir|rechercher|développer|developper|"
    r"caractériser|caracteriser|analyser|concevoir|mettre au point|"
    r"valider|évaluer|evaluer|optimiser|identifier|démontrer|demontrer|"
    r"améliorer|ameliorer|qualifier|réduire|reduire|augmenter|diminuer|"
    r"comprendre|modéliser|modeliser|tester|mesurer|proposer"
    r")\b",
    re.I | re.U,
)

_BIBLIO_CONTEXT_RE = re.compile(
    r"\b(?:"
    r"les auteurs|cette étude|cette etude|une étude|une etude|un article|"
    r"une thèse|une these|travaux de recherche|dans la littérature|dans la litterature|"
    r"publié(?:e)? en|publie(?:e)? en|et al\.|bibliographie|état de l'art|etat de l'art|"
    r"state of the art|prior art|la littérature|la litterature"
    r")\b",
    re.I | re.U,
)

_PROJECT_CONTEXT_RE = re.compile(
    r"\b(?:"
    r"nous avons|nous avions|nos travaux|notre étude|notre etude|notre projet|"
    r"dans le présent projet|dans le present projet|les travaux que nous avons réalisés|"
    r"les travaux de R&D que nous avons réalisés|nous proposons|nous développons|"
    r"nous réalisons|nous avons réalisé|nous avons développé"
    r")\b",
    re.I | re.U,
)

_FRAGMENT_START_RE = re.compile(
    r"^(?:"
    r"[a-zàâäéèêëîïôöùûüç]\S*\s+|"
    r"(?:et|ou|mais|donc|car|ni|or|que|qui|dont|où|si|bien|ainsi|"
    r"de|du|des|le|la|les|un|une|en|au|aux|pour|par|avec|sans)\s+"
    r")",
    re.U,
)


def _is_structural_or_toc_line_universal(text: str) -> bool:
    p = re.sub(r"\s+", " ", str(text or "")).strip()
    if not p:
        return True

    low = _norm_for_match(p)

    if "table des matieres" in low or "sommaire" in low:
        return True

    if _TOC_LINE_UNIVERSAL_RE.match(p) and len(p.split()) <= 18:
        if not _CONJUGATED_VERB_SIGNAL_RE.search(p):
            return True

    # Titre pur court : pas de verbe, pas de ponctuation de phrase.
    if len(p.split()) <= 10 and not re.search(r"[.!?]", p):
        if re.search(
            r"^(?:objectifs?|contexte|etat de l'art|verrous?|demarche|"
            r"resultats?|annexes?|description des travaux|ressources humaines|"
            r"indicateurs?|conclusion|bibliographie|references)",
            low,
            re.I,
        ):
            return True

    return False


def _is_bibliographic_context_universal(text: str) -> bool:
    low = _norm_for_match(text)
    return bool(_BIBLIO_CONTEXT_RE.search(low)) and not _PROJECT_CONTEXT_RE.search(low)


def _should_promote_to_objective_universal(phrase: str, section_role: str = "") -> bool:
    p = re.sub(r"\s+", " ", str(phrase or "")).strip()
    if not p:
        return False
    low = _norm_for_match(p)
    if section_role == "objectifs" and _OBJECTIVE_INFINITIVE_RE.search(p):
        return True
    if re.search(r"\b(?:objectif|vise|afin de|dans le but de|a pour but de|permettre de)\b", low, re.I):
        return True
    return False


# Sauvegarde optionnelle de l'ancienne fonction si besoin debug.
_parse_passage_response_v21 = _parse_passage_response


def _is_artifact_phrase(phrase: str) -> bool:  # type: ignore[override]
    p = re.sub(r"\s+", " ", str(phrase or "")).strip()
    if not p or len(p) < 25:
        return True
    if _is_structural_or_toc_line_universal(p):
        return True
    if _ARTIFACT_PHRASE_RE.match(p):
        return True
    if _FRAGMENT_START_RE.match(p) and not _CONJUGATED_VERB_SIGNAL_RE.search(p):
        return True
    return False


def _is_bad_structural_phrase(phrase: str) -> bool:  # type: ignore[override]
    p = re.sub(r"\s+", " ", str(phrase or "")).strip()
    if not p or len(p) < 25:
        return True
    return _is_structural_or_toc_line_universal(p)


def _parse_passage_response(
    data: dict,
    passage_text: str,
    passage_id: str,
    section_role: str,
) -> PassageMapping:  # type: ignore[override]
    """
    Parsing corrigé universel.
    Ne dépend d'aucun domaine : les reclassements se font par fonction textuelle.
    """
    mapping = PassageMapping(passage_id=passage_id)

    raw_roles = data.get("roles_cir", []) or []
    roles = []
    for r in raw_roles:
        r = str(r or "").strip().lower()
        if r in VALID_ROLES:
            roles.append(r)
    mapping.roles_cir = list(dict.fromkeys(roles)) or ["hors_sujet"]

    kept_evidence_count = 0
    for ev in data.get("evidences", []) or []:
        if kept_evidence_count >= MAX_EVIDENCES_PER_PASSAGE:
            break
        if not isinstance(ev, dict):
            continue

        role = str(ev.get("role", "") or "").strip().lower()
        phrase = re.sub(r"\s+", " ", str(ev.get("phrase_source", "") or "")).strip()

        if role not in VALID_ROLES or not phrase:
            continue

        if _is_artifact_phrase(phrase):
            continue

        # Règle universelle 1 : une phrase bibliographique reste en état de l'art.
        if role in {"resultat", "demarche", "essai", "verrou", "objectif"} and _is_bibliographic_context_universal(phrase):
            role = "etat_art"

        # Règle universelle 2 : dans une section objectifs, les verbes infinitifs
        # de liste sont des objectifs, même si le LLM les a classés en démarche.
        if _should_promote_to_objective_universal(phrase, section_role):
            role = "objectif"

        # Garde-fou existant : un verrou de type spécification devient démarche.
        if role == "verrou" and _is_false_verrou(phrase):
            if _ADMIN_CIR_JUSTIF_RE.search(phrase):
                role = "administratif"
            elif _SPEC_NOT_VERROU_RE.search(phrase):
                role = "demarche"

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

    norm_text = _norm_for_match(passage_text)
    for concept in data.get("concepts", []) or []:
        concept = str(concept or "").strip()
        if not concept or len(concept) < 2:
            continue
        if _norm_for_match(concept) in norm_text:
            mapping.concepts.append(concept)
    mapping.concepts = list(dict.fromkeys(mapping.concepts))

    # Synchronise les rôles après reclassement réel des évidences.
    ev_roles = [e.role for e in mapping.evidences if e.role in VALID_ROLES]
    if ev_roles:
        mapping.roles_cir = list(dict.fromkeys(ev_roles))

    return mapping


# ══════════════════════════════════════════════════════════════════════════════
# PATCH FINAL UNIVERSEL v2.3
# Corrections restantes :
# 1) ne plus limiter à 4 preuves par passage (sinon certains objectifs de liste
#    sont perdus) ;
# 2) récupérer automatiquement les items objectifs en infinitif dans une section
#    objectifs, même si le LLM les oublie ;
# 3) empêcher une démarche au passé de devenir objectif ;
# 4) renforcer le reclassement bibliographique.
# ══════════════════════════════════════════════════════════════════════════════

MAX_EVIDENCES_PER_PASSAGE = 8

_BIBLIO_CONTEXT_RE = re.compile(
    r"\b(?:"
    r"les auteurs|leurs r[ée]sultats|leurs travaux|cette étude|cette etude|"
    r"une étude|une etude|un article|une thèse|une these|travaux de recherche|"
    r"dans la littérature|dans la litterature|publié(?:e)? en|publie(?:e)? en|"
    r"et al\.|bibliographie|état de l'art|etat de l'art|state of the art|"
    r"prior art|la littérature|la litterature|ont étudié|ont etudie|a étudié|"
    r"a etudie|a présenté une étude|a presente une etude|a porté sur|a porte sur"
    r")\b",
    re.I | re.U,
)

_EXPLICIT_OBJECTIVE_RE = re.compile(
    r"\b(?:le pr[ée]sent projet vise|ce projet vise|projet vise|l'objectif est|"
    r"l'objectif était|objectif de|dans l'objectif de|vise à|vise a|a pour objectif|"
    r"a pour but|dans le but de|afin de|pour atteindre cet objectif|nous devons)\b",
    re.I | re.U,
)

_PAST_PROJECT_ACTION_RE = re.compile(
    r"\b(?:nous avons|nous avions|nous sommes|a été|ont été|a ete|ont ete|"
    r"nous avons adopté|nous avons adopte|nous avons réalisé|nous avons realise|"
    r"nous avons mis|nous avons calculé|nous avons calcule|nous avons disposé|"
    r"nous avons dispose|nous avons poursuivi|nous avons développé|nous avons developpe)\b",
    re.I | re.U,
)

_OBJECTIVE_CONTEXT_RE = re.compile(
    r"\b(?:objectifs? vis[ée]s?|pour atteindre cet objectif|nous devons|"
    r"les objectifs? (?:sont|étaient|etaient)|il vise à|il vise a|"
    r"le projet vise|le présent projet vise|le present projet vise)\b",
    re.I | re.U,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;:])\s+|\n+")


def _is_bibliographic_context_universal(text: str) -> bool:  # type: ignore[override]
    low = _norm_for_match(text)
    return bool(_BIBLIO_CONTEXT_RE.search(low)) and not _PROJECT_CONTEXT_RE.search(low)


def _is_clear_objective_phrase_final(phrase: str, section_role: str = "") -> bool:
    p = re.sub(r"\s+", " ", str(phrase or "")).strip()
    if not p or _is_structural_or_toc_line_universal(p):
        return False
    low = _norm_for_match(p)

    # Une action déjà réalisée n'est pas un objectif sauf si elle contient
    # explicitement une formule d'objectif.
    if _PAST_PROJECT_ACTION_RE.search(low) and not _EXPLICIT_OBJECTIVE_RE.search(low):
        return False

    if _EXPLICIT_OBJECTIVE_RE.search(low):
        return True

    if section_role == "objectifs" and _OBJECTIVE_INFINITIVE_RE.search(p):
        return True

    return False


def _extract_objective_items_from_passage_final(passage_text: str, section_role: str) -> list[str]:
    """Extraction déterministe et universelle des objectifs écrits en liste."""
    text = str(passage_text or "")
    if not text.strip():
        return []
    low = _norm_for_match(text)
    has_objective_context = section_role == "objectifs" or bool(_OBJECTIVE_CONTEXT_RE.search(low))
    if not has_objective_context:
        return []

    candidates: list[str] = []

    # 1) Lignes de listes ou phrases séparées par ; . :
    for raw in _SENTENCE_SPLIT_RE.split(text):
        p = re.sub(r"\s+", " ", raw).strip(" \t\r\n-•*;:")
        if not p:
            continue
        if _OBJECTIVE_INFINITIVE_RE.search(p) and len(p) >= 35:
            candidates.append(p)

    # 2) Cas où les puces sont dans un seul paragraphe après "nous devons".
    # On capture les infinitifs jusqu'au prochain ; ou point.
    for m in re.finditer(
        r"(?:Mener|Définir|Definir|Rechercher|Développer|Developper|Caractériser|Caracteriser|Analyser|Concevoir|Valider|Optimiser|Identifier|Évaluer|Evaluer)\b[^.;\n]{25,260}[.;]",
        text,
        flags=re.I | re.U,
    ):
        p = re.sub(r"\s+", " ", m.group(0)).strip(" ;.")
        if p:
            candidates.append(p)

    out: list[str] = []
    seen: set[str] = set()
    for p in candidates:
        if _is_artifact_phrase(p):
            continue
        if not _is_clear_objective_phrase_final(p, section_role):
            continue
        k = _norm_for_match(p)
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


_parse_passage_response_v23_base = _parse_passage_response


def _parse_passage_response(
    data: dict,
    passage_text: str,
    passage_id: str,
    section_role: str,
) -> PassageMapping:  # type: ignore[override]
    mapping = _parse_passage_response_v23_base(data, passage_text, passage_id, section_role)

    # Reclassement final : objectif trop procédural -> démarche.
    for ev in mapping.evidences:
        if ev.role == "objectif" and not _is_clear_objective_phrase_final(ev.phrase_source, section_role):
            if _PAST_PROJECT_ACTION_RE.search(_norm_for_match(ev.phrase_source)):
                ev.role = "demarche"

        # Bibliographie : même si le LLM écrit "résultat", ça reste état de l'art.
        if ev.role in {"resultat", "demarche", "essai", "verrou", "objectif"} and _is_bibliographic_context_universal(ev.phrase_source):
            ev.role = "etat_art"

    # Ajout déterministe des objectifs de liste oubliés par le LLM.
    existing = {_norm_for_match(e.phrase_source) for e in mapping.evidences}
    for phrase in _extract_objective_items_from_passage_final(passage_text, section_role):
        key = _norm_for_match(phrase)
        if key in existing:
            continue
        mapping.evidences.append(
            Evidence(
                role="objectif",
                phrase_source=phrase,
                passage_id=passage_id,
                section_role=section_role,
                confidence=0.85 if section_role == "objectifs" else 0.75,
                validated=True,
            )
        )
        existing.add(key)

    ev_roles = [e.role for e in mapping.evidences if e.role in VALID_ROLES]
    mapping.roles_cir = list(dict.fromkeys(ev_roles)) if ev_roles else ["hors_sujet"]
    return mapping


# ══════════════════════════════════════════════════════════════════════════════
# PATCH RAFFINAGE FINAL UNIVERSEL v2.4
# But : éviter les derniers faux objectifs de protocole et reclasser les
# résultats bibliographiques résiduels dès le mapping des preuves.
# ══════════════════════════════════════════════════════════════════════════════

_EXPLICIT_OBJECTIVE_RE = re.compile(
    r"\b(?:le pr[ée]sent projet vise|le present projet vise|ce projet vise|"
    r"projet vise|l'objectif est|l'objectif était|l'objectif etait|objectif de|"
    r"dans l'objectif de|vise à|vise a|a pour objectif|a pour but|"
    r"dans le but de|pour atteindre cet objectif|nous devons)\b",
    re.I | re.U,
)

_PAST_PROJECT_ACTION_RE = re.compile(
    r"\b(?:nous avons|nous avions|nous sommes|nous les avons|nous l'avons|"
    r"a été|ont été|a ete|ont ete|"
    r"nous avons adopté|nous avons adopte|nous avons réalisé|nous avons realise|"
    r"nous avons mis|nous avons calculé|nous avons calcule|nous avons disposé|"
    r"nous avons dispose|nous avons poursuivi|nous avons développé|nous avons developpe|"
    r"nous avons refroidi|nous avons préparé|nous avons prepare|"
    r"nous avions d'abord|nous avions ensuite|nous avions également|nous avions egalement)\b",
    re.I | re.U,
)

_BIBLIO_RESULT_STYLE_RE = re.compile(
    r"\b(?:les r[ée]sultats ont alors montr[ée]|les r[ée]sultats ont montr[ée]|"
    r"les r[ée]sultats de cette [ée]tude|leurs r[ée]sultats ont|"
    r"les auteurs ont montr[ée]|cette [ée]tude a montr[ée])\b",
    re.I | re.U,
)


def _is_bibliographic_context_universal(text: str) -> bool:  # type: ignore[override]
    low = _norm_for_match(text)
    if _PROJECT_CONTEXT_RE.search(low):
        return False
    if _BIBLIO_CONTEXT_RE.search(low):
        return True
    if _BIBLIO_RESULT_STYLE_RE.search(low):
        return True
    return False


def _is_clear_objective_phrase_final(phrase: str, section_role: str = "") -> bool:  # type: ignore[override]
    p = re.sub(r"\s+", " ", str(phrase or "")).strip()
    if not p or _is_structural_or_toc_line_universal(p):
        return False
    low = _norm_for_match(p)
    if _is_bibliographic_context_universal(p):
        return False
    if re.match(r"^afin de\b", low, re.I) and not _EXPLICIT_OBJECTIVE_RE.search(low):
        return False
    if _PAST_PROJECT_ACTION_RE.search(low) and not _EXPLICIT_OBJECTIVE_RE.search(low):
        return False
    if _EXPLICIT_OBJECTIVE_RE.search(low):
        return True
    if section_role == "objectifs" and _OBJECTIVE_INFINITIVE_RE.search(p):
        return True
    return False

_PARSE_PASSAGE_RESPONSE_V24_BASE = _parse_passage_response


def _parse_passage_response(
    data: dict,
    passage_text: str,
    passage_id: str,
    section_role: str,
) -> PassageMapping:  # type: ignore[override]
    mapping = _PARSE_PASSAGE_RESPONSE_V24_BASE(data, passage_text, passage_id, section_role)

    for ev in mapping.evidences:
        low = _norm_for_match(ev.phrase_source)

        # Faux objectif procédural -> démarche si action réalisée, sinon contexte.
        if ev.role == "objectif" and not _is_clear_objective_phrase_final(ev.phrase_source, section_role):
            if _PAST_PROJECT_ACTION_RE.search(low) or re.match(r"^afin de\b", low, re.I):
                ev.role = "demarche"
            else:
                ev.role = "contexte"

        # Résultat bibliographique -> état de l'art.
        if ev.role in {"resultat", "demarche", "essai", "verrou", "objectif"} and _is_bibliographic_context_universal(ev.phrase_source):
            ev.role = "etat_art"

    ev_roles = [e.role for e in mapping.evidences if e.role in VALID_ROLES]
    mapping.roles_cir = list(dict.fromkeys(ev_roles)) if ev_roles else ["hors_sujet"]
    return mapping


# ══════════════════════════════════════════════════════════════════════════════
# PATCH v2.4 — mode rapide universel : 6–10 appels LLM maximum
# - Les tableaux restent traités sans budget LLM.
# - On garde tous les résultats du document : pas seulement 2024.
# - On priorise les sections utiles, mais on évite les annexes énormes.
# ══════════════════════════════════════════════════════════════════════════════

FAST_MODE_LLM_BUDGET = int(os.getenv("NLP_MAX_LLM_PASSAGES", "10"))
FAST_MODE_MIN_BUDGET = 6

PRIORITY_SECTION_SCORES.update({
    "objectifs": 130,
    "verrous": 125,
    "resultats": 125,
    "travaux": 110,
    "conclusion": 105,
    "travaux_anterieurs": 90,
    "etat_art": 70,
    "contexte": 45,
    "unknown": 35,
    "administratif": -80,
    "titre_motscles": -80,
})

SKIP_SECTION_ROLES.update({"titre_motscles", "administratif", "financial_admin", "annexe"})


# ══════════════════════════════════════════════════════════════════════════════
# PATCH v2.5 — budget LLM adaptatif + passages "unknown" moins pénalisés
# Objectif : documents narratifs (sans titres structurés) → budget élargi.
# ══════════════════════════════════════════════════════════════════════════════

_STRUCTURED_ROLES = frozenset({
    "objectifs", "verrous", "resultats", "travaux", "demarche",
    "etat_art", "conclusion", "travaux_anterieurs",
})


def _count_structured_sections(passages: list) -> int:
    """Compte les passages avec un section_role reconnu (non 'unknown')."""
    return sum(
        1 for p in passages
        if str(_passage_get(p, "section_role", "unknown") or "unknown") in _STRUCTURED_ROLES
    )


def _get_adaptive_llm_budget(passages: list, config_max: int) -> int:
    """
    Budget standard (10) pour docs bien structurés.
    Budget élargi (18) pour docs narratifs (< 15 % de sections reconnues).
    Reste plafonné par config_max.
    """
    if not passages:
        return min(config_max, FAST_MODE_LLM_BUDGET)
    structured = _count_structured_sections(passages)
    ratio = structured / len(passages)
    if ratio < 0.15:
        # Document narratif : on autorise plus d'appels LLM
        budget = min(config_max, 18)
        logger.info(
            "Budget LLM adaptatif : %d/%d passages structurés (ratio=%.0f%%) → budget=%d",
            structured, len(passages), ratio * 100, budget,
        )
        return budget
    return min(config_max, FAST_MODE_LLM_BUDGET)


def _is_useful_for_fast_llm(passage: Any) -> bool:
    text = str(_passage_get(passage, "text", "") or "")
    section_role = str(_passage_get(passage, "section_role", "unknown") or "unknown")
    if not text.strip():
        return False
    if section_role in {"administratif", "financial_admin", "titre_motscles", "annexe"}:
        return False
    if _is_probable_toc_or_title_only(text, section_role):
        return False
    # Les résultats et verrous doivent passer même si le signal est faible.
    if section_role in {"objectifs", "verrous", "resultats", "travaux", "conclusion"}:
        return True
    # Travaux antérieurs : utile pour récupérer les résultats du document complet,
    # mais on limite ensuite le nombre de passages par rôle.
    if section_role == "travaux_anterieurs":
        return bool(re.search(r"r[ée]sultat|essai|simulation|corr[ée]lation|mod[ée]l|verrou|difficult[ée]", text, re.I | re.U))
    # Etat de l'art : uniquement s'il contient un vrai gap/lacune/verrou, pas toute la biblio.
    if section_role == "etat_art":
        return bool(re.search(r"aucune [ée]tude|pas [ée]t[ée] abord[ée]|n['’]?a pas fait l['’]?objet|manque|lacune|verrou|incertitude", text, re.I | re.U))
    # PATCH v2.5 : passages "unknown" avec longueur suffisante + signal → toujours utiles.
    # Typique des docs narratifs sans titres structurés.
    if section_role == "unknown":
        return bool(EVIDENCE_SIGNAL_RE.search(text)) and len(text) > 150
    return bool(EVIDENCE_SIGNAL_RE.search(text))


def _select_passages_for_llm(  # type: ignore[override]
    passages: list[Any],
    max_passages: int = MAX_LLM_PASSAGES,
) -> tuple[list[Any], list[Any], list["PassageMapping"]]:
    # PATCH v2.5 : budget adaptatif selon la structure du document.
    adaptive_budget = _get_adaptive_llm_budget(passages or [], int(max_passages or FAST_MODE_LLM_BUDGET))
    max_passages = max(FAST_MODE_MIN_BUDGET, adaptive_budget)

    table_passages: list[Any] = []
    skipped_mappings: list[PassageMapping] = []
    candidates: list[tuple[int, int, Any]] = []

    for i, p in enumerate(passages or []):
        text = str(_passage_get(p, "text", "") or "")
        passage_id = str(_passage_get(p, "passage_id", "") or f"p{i}")
        section_role = str(_passage_get(p, "section_role", "unknown") or "unknown")
        source_type = str(_passage_get(p, "source_type", "text") or "text")

        if source_type == "table":
            table_passages.append(p)
            continue

        if not _is_useful_for_fast_llm(p):
            role = "administratif" if section_role in {"administratif", "financial_admin"} or ADMIN_SIGNAL_RE.search(text) else "hors_sujet"
            skipped_mappings.append(PassageMapping(passage_id=passage_id, roles_cir=[role], evidences=[], concepts=[], error=None))
            continue

        score = _score_passage_for_llm(p)
        candidates.append((score, i, p))

    # Quotas universels : au moins 1 passage important par catégorie.
    selected: list[Any] = []
    selected_ids: set[str] = set()
    quotas = {
        "objectifs": 1,
        "verrous": 1,
        "resultats": 2,
        "travaux": 2,
        "conclusion": 1,
        "travaux_anterieurs": 2,
        "etat_art": 1,
        # PATCH v2.5 : inclure les passages "unknown" signalés dans les quotas
        # pour ne pas les laisser hors budget sur les docs narratifs.
        "unknown": 3,
    }

    def add_passage(p: Any) -> None:
        pid = str(_passage_get(p, "passage_id", "") or "")
        if pid and pid not in selected_ids and len(selected) < max_passages:
            selected.append(p)
            selected_ids.add(pid)

    # D'abord : meilleurs par rôle selon quotas.
    for role, quota in quotas.items():
        role_candidates = [(s, i, p) for (s, i, p) in candidates if str(_passage_get(p, "section_role", "unknown") or "unknown") == role]
        role_candidates.sort(key=lambda x: (-x[0], x[1]))
        for _, _, p in role_candidates[:quota]:
            add_passage(p)

    # Ensuite : compléter avec les meilleurs restants.
    candidates.sort(key=lambda x: (-x[0], x[1]))
    for _, _, p in candidates:
        if len(selected) >= max_passages:
            break
        add_passage(p)

    # Ordre documentaire.
    order = {str(_passage_get(p, "passage_id", "") or f"p{i}"): i for i, p in enumerate(passages or [])}
    selected.sort(key=lambda p: order.get(str(_passage_get(p, "passage_id", "")), 10**9))
    return selected, table_passages, skipped_mappings