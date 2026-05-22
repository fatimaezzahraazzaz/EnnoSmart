"""
modules/NLP/section_extractor.py — NLP V7 SECTION-FIRST
──────────────────────────────────────────────────────────────────────────────
Extraction CIR ciblée par section documentaire.

Principe :
- On ne demande plus au LLM de deviner librement le rôle d'un passage.
- document_structure_mapper a déjà classé la section : objectifs, verrous,
  travaux, résultats, etc.
- Le prompt est donc spécialisé selon la section.

Sortie : EvidenceMapResult compatible avec aggregator.py.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
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

try:
    from modules.NLP.evidence_mapper import Evidence, PassageMapping, EvidenceMapResult
except Exception:  # fallback pour test isolé
    @dataclass
    class Evidence:
        role: str
        phrase_source: str
        passage_id: str
        section_role: str = "unknown"
        confidence: float = 0.7
        validated: bool = False
        def to_dict(self) -> dict:
            return self.__dict__

    @dataclass
    class PassageMapping:
        passage_id: str
        roles_cir: list[str] = field(default_factory=list)
        evidences: list[Evidence] = field(default_factory=list)
        concepts: list[str] = field(default_factory=list)
        structured_entities: dict = field(default_factory=dict)
        error: Optional[str] = None
        def to_dict(self) -> dict:
            return self.__dict__

    @dataclass
    class EvidenceMapResult:
        mappings: list[PassageMapping] = field(default_factory=list)
        llm_calls: int = 0
        backend: str = "unknown"
        model: str = ""
        processing_time: float = 0.0
        errors: list[str] = field(default_factory=list)
        def to_dict(self) -> dict:
            return {"mappings": [m.to_dict() for m in self.mappings]}

logger = logging.getLogger(__name__)

DEFAULT_LLM_MODEL = "ollama:qwen3:4b-instruct"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
LOCAL_MODEL_PREFIXES = ("ollama:", "local:")
TIMEOUT_SECONDS = 120
MAX_RETRIES = 1
MAX_SECTION_CHARS = 9000
MAX_SECTIONS_PER_ROLE = {
    "titre_motscles": 3,
    "objectifs": 4,
    "verrous": 5,
    "etat_art": 4,
    "travaux": 8,
    "demarche": 6,
    "essais": 5,
    "resultats": 6,
    "conclusion": 4,
    "contexte": 3,
    "unknown": 4,
}

TARGET_ROLES_BY_SECTION = {
    "titre_motscles": ["preuve"],
    "contexte": ["contexte", "objectif"],
    "objectifs": ["objectif", "contexte"],
    "etat_art": ["etat_art", "verrou"],
    "verrous": ["verrou"],
    "travaux": ["demarche", "essai", "resultat", "verrou"],
    "demarche": ["demarche", "essai"],
    "essais": ["essai", "resultat", "metrique"],
    "resultats": ["resultat", "metrique"],
    "conclusion": ["resultat", "objectif", "verrou"],
    "unknown": ["objectif", "verrou", "etat_art", "demarche", "essai", "resultat"],
}

VALID_CIR_ROLES = {"contexte", "objectif", "verrou", "etat_art", "demarche", "essai", "resultat", "preuve", "metrique"}

ROLE_DESCRIPTIONS = {
    "objectif": "but à atteindre, ambition R&D, problème que l'opération cherche à résoudre",
    "verrou": "limite, incertitude, difficulté non résolue, absence de méthode, obstacle scientifique ou technique",
    "etat_art": "connaissances existantes, littérature, solutions connues et leurs limites",
    "demarche": "actions réalisées, conception, choix, méthode, développement, simulation, modélisation",
    "essai": "test, expérimentation, validation, protocole, mesure, campagne d'essais",
    "resultat": "constat obtenu, performance mesurée, observation issue des essais/simulations, validation",
    "contexte": "contexte métier ou scientifique du projet",
    "metrique": "chiffre, indicateur, mesure, score, performance quantitative",
    "preuve": "information structurante utile mais ne correspondant pas aux autres rôles",
}


def _is_local_model(model: str) -> bool:
    return str(model or "").startswith(LOCAL_MODEL_PREFIXES)


def _clean_model_name(model: str) -> str:
    m = str(model or DEFAULT_LLM_MODEL).strip()
    for prefix in LOCAL_MODEL_PREFIXES:
        if m.startswith(prefix):
            return m[len(prefix):]
    return m


def _get_openrouter_client():
    if OpenAI is None:
        return None
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)


def _extract_json(raw: str) -> Optional[dict]:
    content = str(raw or "").strip()
    if not content:
        return None
    variants = [content, re.sub(r"```(?:json)?|```", "", content, flags=re.I).strip()]
    for v in variants:
        try:
            data = json.loads(v)
            return data if isinstance(data, dict) else None
        except Exception:
            pass
    m = re.search(r"\{.*\}", content, flags=re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else None
        except Exception:
            return None
    return None


def _trim_section(text: str) -> str:
    clean = re.sub(r"\n{3,}", "\n\n", str(text or "")).strip()
    if len(clean) <= MAX_SECTION_CHARS:
        return clean
    return clean[:MAX_SECTION_CHARS] + "\n[SECTION_TRONQUÉE]"


def _contains_exact(section_text: str, phrase: str) -> bool:
    if not phrase:
        return False
    # Tolérance simple aux espaces.
    s = re.sub(r"\s+", " ", str(section_text or "")).lower()
    p = re.sub(r"\s+", " ", str(phrase or "")).strip().lower()
    return len(p) >= 12 and p in s


def _clean_phrase(text: Any) -> str:
    phrase = re.sub(r"\s+", " ", str(text or "")).strip(" \t\n\r;:,.|")
    if len(phrase) > 900:
        phrase = phrase[:900].rsplit(" ", 1)[0] + "..."
    return phrase


def _section_role(section: Any) -> str:
    if isinstance(section, dict):
        return str(section.get("role", "unknown") or "unknown")
    return str(getattr(section, "role", "unknown") or "unknown")


def _section_id(section: Any, idx: int) -> str:
    if isinstance(section, dict):
        return str(section.get("section_id", f"S{idx:04d}") or f"S{idx:04d}")
    return str(getattr(section, "section_id", f"S{idx:04d}") or f"S{idx:04d}")


def _section_title(section: Any) -> str:
    if isinstance(section, dict):
        return str(section.get("title", "") or "")
    return str(getattr(section, "title", "") or "")


def _section_content(section: Any) -> str:
    if isinstance(section, dict):
        return str(section.get("content", "") or "")
    return str(getattr(section, "content", "") or "")


def _prompt_for_section(title: str, role: str, content: str) -> str:
    allowed = TARGET_ROLES_BY_SECTION.get(role, TARGET_ROLES_BY_SECTION["unknown"])
    allowed_desc = "\n".join(f"- {r}: {ROLE_DESCRIPTIONS.get(r, r)}" for r in allowed)

    role_rules = {
        "objectifs": "Extrais surtout les objectifs R&D. Ne prends pas les solutions déjà réalisées, ni les résultats, ni les détails de mise en œuvre.",
        "verrous": "Extrais uniquement les verrous/incertitudes. Un verrou exprime une limite, une absence de méthode, une difficulté ou une incertitude non résolue.",
        "etat_art": "Extrais les éléments d'état de l'art et les limites des travaux existants. Si une limite fonde un verrou, classe-la aussi en verrou.",
        "travaux": "Extrais les démarches, méthodes, simulations, prototypes, essais et résultats éventuels. Ne classe en verrou que les phrases qui expriment explicitement une difficulté ou une incertitude.",
        "demarche": "Extrais les méthodes, choix techniques, étapes de conception, protocoles et essais.",
        "essais": "Extrais les essais, protocoles, mesures, métriques et résultats observés.",
        "resultats": "Extrais uniquement les résultats, constats, performances et observations obtenues.",
        "conclusion": "Extrais les résultats/contributions/perspectives, et seulement les objectifs ou verrous rappelés explicitement.",
        "contexte": "Extrais le contexte utile et les objectifs si la section exprime clairement le but de l'opération.",
        "unknown": "La section est non structurée : extrais prudemment les rôles CIR uniquement si la phrase est explicite.",
    }.get(role, "Extrais les informations CIR utiles sans inventer.")

    return f"""Tu es un extracteur CIR/R&D section-first.

SECTION DOCUMENTAIRE
- Titre : {title}
- Rôle de section détecté : {role}

RÈGLE SPÉCIALE POUR CETTE SECTION
{role_rules}

RÔLES AUTORISÉS DANS CETTE SECTION
{allowed_desc}

RÈGLES ABSOLUES
1. Réponds uniquement en JSON valide.
2. Copie uniquement des phrases exactes présentes dans la section.
3. N'invente rien. Pas de paraphrase dans phrase_source.
4. Une phrase technique de solution déjà réalisée n'est pas un objectif.
5. Un résultat de simulation n'est pas un verrou sauf si la phrase formule explicitement une incertitude/limite.
6. Une phrase doit être utile pour un dossier CIR/R&D.
7. Ne retourne pas les titres seuls.

FORMAT JSON STRICT
{{
  "items": [
    {{"role": "objectif|verrou|etat_art|demarche|essai|resultat|contexte|metrique|preuve", "phrase_source": "phrase exacte", "confidence": 0.0}}
  ],
  "concepts": ["concept technique central", "..."]
}}

SECTION À ANALYSER
{_trim_section(content)}
"""


def _call_llm(title: str, role: str, content: str, model: str) -> tuple[Optional[dict], str, Optional[str]]:
    prompt = _prompt_for_section(title, role, content)
    if _is_local_model(model):
        if ollama is None:
            return None, "ollama", "ollama non installé"
        try:
            response = ollama.chat(
                model=_clean_model_name(model),
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.0, "num_ctx": 8192},
            )
            raw = response.get("message", {}).get("content", "")
            return _extract_json(raw), "ollama", None
        except Exception as exc:
            return None, "ollama", str(exc)

    client = _get_openrouter_client()
    if client is None:
        return None, "openrouter", "client OpenAI/OpenRouter indisponible"
    try:
        response = client.chat.completions.create(
            model=str(model or DEFAULT_LLM_MODEL),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            timeout=TIMEOUT_SECONDS,
        )
        raw = response.choices[0].message.content or ""
        return _extract_json(raw), "openrouter", None
    except Exception as exc:
        return None, "openrouter", str(exc)


def _fallback_extract(section: Any, idx: int) -> PassageMapping:
    """Fallback sans LLM : utile si modèle indisponible. Conservateur."""
    sid = _section_id(section, idx)
    role = _section_role(section)
    content = _section_content(section)
    title = _section_title(section)
    allowed = TARGET_ROLES_BY_SECTION.get(role, [])
    evidences: list[Evidence] = []
    patterns = {
        "objectif": re.compile(r"(objectif|vise|enjeu|but|afin de|permettre de|chercher [àa])", re.I),
        "verrou": re.compile(r"(verrou|incertitude|difficult[ée]|absence de|limite|manque|insuffisamment|ne permet pas)", re.I),
        "resultat": re.compile(r"(r[ée]sultat|montre|obtenu|performance|gain|r[ée]duction|am[ée]lioration|observe)", re.I),
        "demarche": re.compile(r"(nous avons|m[ée]thode|approche|simulation|mod[ée]lisation|conception|d[ée]velopp)", re.I),
        "etat_art": re.compile(r"([ée]tat de l['’]?art|litt[ée]rature|travaux existants|publication)", re.I),
        "essai": re.compile(r"(essai|test|mesure|protocole|validation)", re.I),
    }
    sentences = re.split(r"(?<=[.!?])\s+|\n+", content)
    for sent in sentences:
        s = _clean_phrase(sent)
        if len(s) < 40:
            continue
        for r in allowed:
            pat = patterns.get(r)
            if pat and pat.search(s):
                evidences.append(Evidence(role=r, phrase_source=s, passage_id=sid, section_role=role, confidence=0.55, validated=True))
                break
        if len(evidences) >= 5:
            break
    return PassageMapping(passage_id=sid, roles_cir=sorted(set(e.role for e in evidences)) or ["hors_sujet"], evidences=evidences, concepts=[], error=None)


def _parse_llm_items(data: dict, section: Any, idx: int) -> PassageMapping:
    sid = _section_id(section, idx)
    section_role = _section_role(section)
    content = _section_content(section)
    allowed = set(TARGET_ROLES_BY_SECTION.get(section_role, TARGET_ROLES_BY_SECTION["unknown"]))
    evidences: list[Evidence] = []
    concepts: list[str] = []

    for c in data.get("concepts", []) or []:
        c = _clean_phrase(c)
        if 2 <= len(c) <= 120:
            concepts.append(c)

    for item in data.get("items", []) or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "") or "").strip().lower()
        phrase = _clean_phrase(item.get("phrase_source", ""))
        if role not in VALID_CIR_ROLES or role not in allowed:
            continue
        if len(phrase) < 25 or not _contains_exact(content, phrase):
            continue
        try:
            conf = float(item.get("confidence", 0.72) or 0.72)
        except Exception:
            conf = 0.72
        evidences.append(Evidence(
            role=role,
            phrase_source=phrase,
            passage_id=sid,
            section_role=section_role,
            confidence=max(0.0, min(conf, 1.0)),
            validated=True,
        ))

    roles = sorted(set(e.role for e in evidences)) or ["hors_sujet"]
    return PassageMapping(passage_id=sid, roles_cir=roles, evidences=evidences, concepts=concepts, error=None)


def _select_sections(sections: list[Any], max_sections: int | None = None) -> list[tuple[int, Any]]:
    by_role_count: dict[str, int] = {}
    selected: list[tuple[int, Any]] = []
    priority = {
        "objectifs": 100,
        "verrous": 98,
        "etat_art": 90,
        "travaux": 88,
        "demarche": 86,
        "essais": 84,
        "resultats": 82,
        "conclusion": 78,
        "titre_motscles": 70,
        "contexte": 65,
        "unknown": 40,
        "administratif": -50,
        "annexe": -30,
    }
    scored = []
    for idx, sec in enumerate(sections or []):
        role = _section_role(sec)
        content = _section_content(sec)
        if role == "administratif" or len(content.strip()) < 40:
            continue
        score = priority.get(role, 30) + min(len(content) // 1000, 8)
        scored.append((score, idx, sec))
    scored.sort(key=lambda x: (-x[0], x[1]))

    for _, idx, sec in scored:
        role = _section_role(sec)
        limit = MAX_SECTIONS_PER_ROLE.get(role, 3)
        if by_role_count.get(role, 0) >= limit:
            continue
        selected.append((idx, sec))
        by_role_count[role] = by_role_count.get(role, 0) + 1
        if max_sections and len(selected) >= max_sections:
            break
    selected.sort(key=lambda x: x[0])
    return selected


def extract_cir_from_sections(
    sections: list[Any],
    model: str = DEFAULT_LLM_MODEL,
    enabled: bool = True,
    max_sections: int | None = 28,
) -> EvidenceMapResult:
    """Extrait les preuves CIR par section et retourne un EvidenceMapResult compatible."""
    t0 = time.time()
    result = EvidenceMapResult(model=model)
    selected = _select_sections(sections, max_sections=max_sections)

    if not enabled:
        result.mappings = [_fallback_extract(sec, idx) for idx, sec in selected]
        result.backend = "fallback"
        result.processing_time = round(time.time() - t0, 2)
        return result

    backend = "unknown"
    for idx, section in selected:
        title = _section_title(section)
        role = _section_role(section)
        content = _section_content(section)
        data = None
        err = None
        for attempt in range(MAX_RETRIES + 1):
            data, backend, err = _call_llm(title, role, content, model=model)
            if isinstance(data, dict):
                break
        if not isinstance(data, dict):
            mapping = _fallback_extract(section, idx)
            mapping.error = err
            result.errors.append(f"{_section_id(section, idx)}: {err}")
        else:
            mapping = _parse_llm_items(data, section, idx)
        result.mappings.append(mapping)
        result.llm_calls += 1 if isinstance(data, dict) else 0

    result.backend = backend
    result.processing_time = round(time.time() - t0, 2)
    logger.info("Section extractor : %d sections | %d preuves | backend=%s", len(selected), len(result.all_evidences()) if hasattr(result, 'all_evidences') else 0, backend)
    return result


if __name__ == "__main__":
    print("section_extractor.py loaded")
