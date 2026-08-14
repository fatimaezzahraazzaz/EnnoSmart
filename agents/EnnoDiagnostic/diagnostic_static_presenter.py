# -*- coding: utf-8 -*-
from __future__ import annotations

"""EnnoDiagnostic V191 — sections structurées, projet courant et PydanticAI robuste.

Ce module remplace `agents/EnnoDiagnostic/diagnostic_static_presenter.py`.
Il conserve les deux fonctions attendues par `ennodiagnostic_agent.py` :
`generate_structured_diagnostic_core` et `build_final_static_diagnostic`.

Principes :
- un appel LLM par section afin d'appliquer un vrai budget de tokens ;
- des preuves différentes selon le rôle NLP de la section ;
- aucun nom de fichier dans le texte rédigé ;
- les documents et passages restent dans `evidence` pour le frontend ;
- sortie compatible avec l'ancien frontend via `sections_by_title` ;
- télémétrie de tokens dans `token_usage_by_section`.
"""

import hashlib
import json
import os
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from .section_context_config import (
        SectionContextConfig,
        all_section_configs,
        estimate_tokens,
    )
except Exception:
    from section_context_config import (  # type: ignore
        SectionContextConfig,
        all_section_configs,
        estimate_tokens,
    )


# PydanticAI : sortie structurée de la conclusion d'éligibilité.
# Si la dépendance n'est pas installée, le presenter conserve temporairement
# l'ancien chemin LLM afin de ne pas casser le reste du diagnostic.
try:
    from .structured_eligibility_writer import generate_eligibility_section_with_pydantic_ai
except Exception:
    try:
        from structured_eligibility_writer import generate_eligibility_section_with_pydantic_ai  # type: ignore
    except Exception:
        generate_eligibility_section_with_pydantic_ai = None  # type: ignore


STATIC_SECTION_DEFINITIONS: List[Dict[str, str]] = [
    {
        "key": "lecture_frascati",
        "title": "Étude d'éligibilité",
        "description": (
            "Couverture Frascati et nature de la démarche analysées séparément, "
            "puis synthétisées en recommandation projet à valider."
        ),
    },
    {
        "key": "memoire_v2",
        "title": "Mémoire V2",
        "description": "Utilisation de la mémoire comme style et orientation, jamais comme preuve factuelle.",
    },
    {
        "key": "synthese_strategique",
        "title": "Synthèse stratégique du projet",
        "description": "Résumé du projet, de son enjeu technique et de sa logique R&D.",
    },
    {
        "key": "objectif_global",
        "title": "Objectif global du projet",
        "description": "Objectif technique reformulé dans une logique CIR.",
    },
    {
        "key": "verrous_rnd",
        "title": "Signaux de verrous R&D candidats",
        "description": "Donnée conservée pour compatibilité et synchronisation ; la carte dupliquée doit être masquée côté frontend.",
    },
    {
        "key": "demarche_detectee",
        "title": "Démarche détectée",
        "description": "Méthodes détectées et contrôle de leur nécessité face aux incertitudes du projet.",
    },
    {
        "key": "resultats_metriques",
        "title": "Résultats / métriques",
        "description": "Résultats chiffrés, observations qualitatives et portée des résultats disponibles.",
    },
    {
        "key": "parametres_contraintes",
        "title": "Paramètres et contraintes techniques",
        "description": "Conditions physiques, numériques, expérimentales ou de performance.",
    },
]

KEY_TO_TITLE = {item["key"]: item["title"] for item in STATIC_SECTION_DEFINITIONS}

CORE_LLM_KEYS = [
    "synthese_strategique",
    "objectif_global",
    "justification_frascati",
    "demarche_detectee",
    "resultats_metriques",
    "parametres_contraintes",
]


def _current_project_only_mode() -> bool:
    """
    Mode de sûreté par défaut : aucune donnée textuelle provenant d'un autre
    projet, de Memory V2, d'exemples de style ou d'un CIR précédent n'est
    injectée dans les prompts qui décrivent le projet courant.

    Mettre ENNOSMART_DIAG_CURRENT_PROJECT_ONLY=0 uniquement si l'on souhaite
    explicitement réactiver l'ancien comportement.
    """
    return str(os.getenv("ENNOSMART_DIAG_CURRENT_PROJECT_ONLY", "1")).strip().lower() not in {
        "0", "false", "no", "off"
    }


_REASONING_PATTERNS = [
    r"\bwe need to\b",
    r"\bwe must\b",
    r"\blet'?s craft\b",
    r"\bthe user wants\b",
    r"\bchain of thought\b",
    r"\bje dois produire\b",
    r"\bje vais rédiger\b",
]

_FILE_REFERENCE_RE = re.compile(
    r"\b(?:dans|selon|d'après)\s+[^,;\n]{1,220}\.(?:pdf|docx?|docm|msg|pptx?|xlsx?|txt)\s*[,;:]?\s*",
    flags=re.I,
)

_SOURCE_LINE_RE = re.compile(
    r"^\s*(?:source|sources|indice source|preuve source|document source)\s*(?:\d+)?\s*[:\-–—]",
    flags=re.I,
)


_INTERNAL_EVIDENCE_TOKEN_RE = re.compile(r"\b(?:E\d+|F\d+|G\d+\.S\d+)\b", flags=re.I)


# Termes signalant souvent un changement de domaine ou d'objet technique. Ils
# ne sont pas interdits : ils sont acceptés lorsqu'ils figurent dans les preuves
# de la section. Cette liste est générique et ne contient aucune famille de
# verrou ni aucun identifiant de projet.
DOMAIN_SHIFT_TERMS = {
    "batiment", "immeuble", "chantier", "construction", "logement", "maison",
    "vehicule", "automobile", "avion", "aeronef", "navire", "ferroviaire",
    "patient", "clinique", "hopital", "medical", "pharmaceutique",
    "agricole", "agriculture", "alimentaire", "cosmetique",
    "logiciel", "application mobile", "cloud", "base de donnees",
    "climatisation", "chauffage", "ventilation", "bureaux", "habitation",
}


def strip_internal_evidence_references(value: Any) -> str:
    """Retire les identifiants internes du texte consultant, jamais des métadonnées."""
    text = str(value or "")
    ref = r"(?:E\d+|G\d+\.S\d+)"
    refs = rf"{ref}(?:\s*(?:,|;|et|ou)\s*{ref})*"

    # Formes sujet : « L'évidence E4 souligne que ... ».
    text = re.sub(
        rf"(?i)(?:l['’])?(?:évidence|evidence|preuve|indice)\s+{refs}\s+"
        rf"(?:souligne|indique|montre|précise|confirme|suggère|établit)\s+que\s+",
        "",
        text,
    )
    # Formes incidentes : « comme mentionné dans l'évidence E5 et E6 ».
    text = re.sub(
        rf"(?i)(?:comme\s+(?:mentionné|indiqué|décrit)\s+)?(?:dans|selon|d'après|par)?\s*"
        rf"(?:l['’])?(?:évidence|evidence|preuve|indice)\s+{refs}",
        "",
        text,
    )
    # Formes où les codes internes sont directement le sujet de la phrase.
    text = re.sub(
        rf"(?i)(?:{refs})\s+(?:indique(?:nt)?|montre(?:nt)?|souligne(?:nt)?|"
        rf"précise(?:nt)?|confirme(?:nt)?|suggère(?:nt)?)\s+(?:que\s+)?",
        "",
        text,
    )
    # Références entre parenthèses et éventuels codes résiduels.
    text = re.sub(rf"(?i)\(\s*(?:évidence|evidence|preuve|indice|source)?\s*{refs}\s*\)", "", text)
    text = _INTERNAL_EVIDENCE_TOKEN_RE.sub("", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r"([,;:])\s*[,;:]", r"\1 ", text)
    text = re.sub(
        r",\s+(?=(?:indique|montre|souligne|précise|confirme|suggère)(?:nt|s)?\b)",
        " ",
        text,
        flags=re.I,
    )
    text = re.sub(r"\s{2,}", " ", text)

    cleaned_lines: List[str] = []
    for line in text.splitlines():
        line = line.strip(" \t,;:–—-")
        if line:
            line = line[0].upper() + line[1:]
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def clean_text(value: Any, max_chars: int = 5000) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def has_reasoning_leak(value: Any) -> bool:
    text = clean_text(value, 12000).lower()
    return any(re.search(pattern, text, flags=re.I) for pattern in _REASONING_PATTERNS)


def strip_markdown(value: Any, max_chars: int = 5000) -> str:
    text = clean_text(value, max_chars=max_chars * 2)
    if not text:
        return ""
    text = re.sub(r"```(?:json|markdown|md)?", "", text, flags=re.I).replace("```", "")
    text = re.sub(r"(?m)^\s*#{1,6}\s+", "", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    return clean_text(text, max_chars=max_chars)


def sanitize_generated_text(value: Any, max_chars: int = 2400) -> str:
    """Nettoie le texte visible et retire les références qui doivent rester séparées."""
    text = strip_markdown(value, max_chars=max_chars * 2)
    if not text or has_reasoning_leak(text):
        return ""

    kept: List[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or _SOURCE_LINE_RE.match(line):
            continue
        line = _FILE_REFERENCE_RE.sub("", line)
        line = strip_internal_evidence_references(line)
        line = re.sub(r"\bSources?\s+\d+(?:\s*[,;]\s*\d+)*\b", "", line, flags=re.I)
        line = re.sub(r"\bIndice\s*:\s*", "", line, flags=re.I)
        line = clean_text(line, max_chars=max_chars)
        if line:
            kept.append(line)

    result = clean_text("\n".join(kept), max_chars=max_chars)
    return "" if has_reasoning_leak(result) else result


def extract_json_object(raw: Any) -> Dict[str, Any]:
    """Récupère un objet JSON même si le client LLM renvoie déjà un dict
    ou enveloppe la réponse dans content/result/output/data.

    L'ancien code transformait systématiquement ``raw`` en chaîne. Si
    ``llm.generate(..., json_mode=True)`` renvoyait déjà un dictionnaire,
    ``str(dict)`` produisait des quotes Python et le parseur retournait {},
    ce qui faisait croire au garde-fou que le texte était vide.
    """
    if isinstance(raw, dict):
        data = raw
    else:
        text = clean_text(raw, max_chars=60000)
        if not text:
            return {}
        text = re.sub(r"```(?:json)?", "", text, flags=re.I).replace("```", "").strip()
        try:
            parsed = json.loads(text)
            data = parsed if isinstance(parsed, dict) else {}
        except Exception:
            data = {}
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(text[start:end + 1])
                    data = parsed if isinstance(parsed, dict) else {}
                except Exception:
                    data = {}

    # Certains wrappers LLM renvoient {"content": "{...}"} ou
    # {"result": {...}}. On déroule au maximum trois enveloppes.
    for _ in range(3):
        if any(key in data for key in ("paragraphs", "paragraph", "items", "text", "body", "claims")):
            break
        unwrapped = None
        for key in ("data", "result", "output", "response", "content", "message", "json"):
            value = data.get(key) if isinstance(data, dict) else None
            if isinstance(value, dict):
                unwrapped = value
                break
            if isinstance(value, str) and value.strip():
                nested = extract_json_object(value)
                if nested:
                    unwrapped = nested
                    break
        if not isinstance(unwrapped, dict):
            break
        data = unwrapped
    return data if isinstance(data, dict) else {}


def meta_of(source: Dict[str, Any]) -> Dict[str, Any]:
    meta = source.get("metadata") if isinstance(source, dict) else {}
    return meta if isinstance(meta, dict) else {}


def source_text(source: Dict[str, Any]) -> str:
    """Retourne le passage avec son contexte local quand le NLP l'a conservé.

    Les extractions PDF/MSG découpent parfois une phrase juste avant le nom du
    paramètre ou de l'objectif. ``analysis_text`` contient alors le fragment
    avec ``context_before``/``context_after`` et est plus fidèle que ``text``
    seul. On ne va jamais chercher du contenu hors du projet courant.
    """
    if not isinstance(source, dict):
        return ""
    meta = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    raw = clean_text(
        source.get("text")
        or source.get("source_text")
        or source.get("content")
        or source.get("excerpt")
        or "",
        max_chars=4000,
    )
    analysis = clean_text(
        source.get("analysis_text")
        or meta.get("analysis_text")
        or "",
        max_chars=5200,
    )
    if analysis and len(analysis) >= max(60, int(len(raw) * 0.8)):
        return analysis
    before = clean_text(source.get("context_before") or meta.get("context_before") or "", 1200)
    after = clean_text(source.get("context_after") or meta.get("context_after") or "", 1200)
    expanded = clean_text(" ".join(part for part in (before, raw, after) if part), 5200)
    return expanded or raw


def source_value(source: Dict[str, Any], *keys: str) -> Any:
    meta = meta_of(source)
    for key in keys:
        value = meta.get(key)
        if value not in (None, ""):
            return value
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def source_doc(source: Dict[str, Any]) -> str:
    return clean_text(
        source_value(source, "document", "filename", "source_name") or "",
        max_chars=260,
    )


def source_score(source: Dict[str, Any]) -> float:
    meta = meta_of(source)
    score = 0.0
    for key, weight in (
        ("rank_score", 2.0),
        ("confidence", 1.3),
        ("frascati_score", 1.1),
        ("verrou_score", 1.1),
    ):
        try:
            score += float(meta.get(key) or source.get(key) or 0.0) * weight
        except Exception:
            continue
    if len(source_text(source)) > 100:
        score += 0.25
    if source_doc(source):
        score += 0.1
    return score


def _source_signature(source: Dict[str, Any]) -> Tuple[str, str]:
    chunk_id = clean_text(source_value(source, "rag_chunk_id", "chunk_id", "passage_id", "id") or "")
    if chunk_id:
        return "id", chunk_id
    normalized = re.sub(r"\s+", " ", source_text(source)[:320]).lower()
    return source_doc(source).lower(), normalized


def get_sources(
    sections: Dict[str, List[Dict[str, Any]]],
    keys: Sequence[str],
    max_items: int,
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for key in keys:
        values = sections.get(key) if isinstance(sections, dict) else []
        if isinstance(values, list):
            candidates.extend(item for item in values if isinstance(item, dict) and source_text(item))

    seen = set()
    output: List[Dict[str, Any]] = []
    for source in sorted(candidates, key=source_score, reverse=True):
        signature = _source_signature(source)
        if signature in seen:
            continue
        seen.add(signature)
        output.append(source)
        if len(output) >= max_items:
            break
    return output



# ---------------------------------------------------------------------------
# Sélection générique des preuves par section
# ---------------------------------------------------------------------------

_SECTION_REFERENCE_RE = re.compile(
    r"(?:\bet\s+al\.\b|\bdoi\s*:?|https?://doi\.org|\bbibliograph(?:y|ie)\b|"
    r"\breferences?\b|\btravaux connexes\b|\brelated works?\b|"
    r"\bvol\.\s*\d+|\bpp?\.\s*\d+|\bproceedings\b|\bjournal\b)",
    re.I,
)
_SECTION_METADATA_RE = re.compile(
    r"^(?:to cite this version|authors?|auteurs?|affiliations?|references?|bibliograph(?:y|ie)|"
    r"table of contents|table des mati[eè]res|list of figures|table des illustrations)\s*:?$",
    re.I,
)
_OBJECTIVE_MARKERS_RE = re.compile(
    r"\b(?:objectif|but|finalit[eé]|vise [aà]|aims? to|goal|purpose|this work aims|"
    r"we propose|nous proposons|contribution|chercher [aà]|[eé]valuer|comparer|d[eé]terminer|"
    r"quantifier|valider|d[eé]montrer|caract[eé]riser)\b",
    re.I,
)
_PROJECT_ACTION_RE = re.compile(
    r"\b(?:nous avons|nous utilisons|nous proposons|nous comparons|nous [eé]valuons|"
    r"we (?:use|used|propose|proposed|compare|compared|evaluate|evaluated|train|trained|"
    r"measure|measured|conduct|conducted|perform|performed)|dans ce projet|our (?:work|study|method|approach))\b",
    re.I,
)
_PARAMETER_RE = re.compile(
    r"\b(?:param[eè]tre|configuration|condition|angle|incidence|azimut|gisement|densit[eé]|"
    r"maillage|mesh|ray|rayon|fr[eé]quence|seuil|tol[eé]rance|nombre|pas de|step|"
    r"dataset|jeu de donn[eé]es|mat[eé]riau|material|mod[eè]le 3d|cad|cao|"
    r"temp[eé]rature|pression|d[eé]bit|dimension|taille|r[eé]solution)\b",
    re.I,
)
_RESULT_RE = re.compile(
    r"\b(?:r[eé]sultat|result|accuracy|pr[eé]cision|performance|score|gain|[eé]cart|"
    r"augmentation|diminution|improvement|increase|decrease|confusion|mesur[eé]|observ[eé])\b",
    re.I,
)
_METHOD_RE = re.compile(
    r"\b(?:m[eé]thode|protocole|exp[eé]rience|exp[eé]rimentation|essai|test|entra[iî]nement|"
    r"training|simulation|comparaison|ablation|validation|mesure|production param[eé]trique)\b",
    re.I,
)
_QUANT_RE = re.compile(r"(?<![A-Za-z0-9])[-+]?\d+(?:[.,]\d+)?(?:\s*%|\s*°|\s*[A-Za-z]{1,5})?")


def _source_role(source: Dict[str, Any]) -> str:
    meta = meta_of(source)
    return clean_text(
        meta.get("role") or meta.get("final_role") or source.get("role") or "",
        80,
    ).lower()


def _source_section_title(source: Dict[str, Any]) -> str:
    meta = meta_of(source)
    return clean_text(meta.get("section_title") or meta.get("section") or "", 260)


def _source_reference_like(source: Dict[str, Any]) -> bool:
    """Détecte une citation bibliographique sans jeter un chunk scientifique substantiel."""
    text = source_text(source)
    section = _source_section_title(source)
    substantive = bool(
        len(text) >= 80
        and (
            _OBJECTIVE_MARKERS_RE.search(text)
            or _PROJECT_ACTION_RE.search(text)
            or _METHOD_RE.search(text)
            or _RESULT_RE.search(text)
        )
    )
    if _SECTION_METADATA_RE.fullmatch(section.strip()) and not substantive:
        return True
    hits = len(_SECTION_REFERENCE_RE.findall(f"{section} {text}"))
    return hits >= 2 and not _PROJECT_ACTION_RE.search(text)


def _section_source_score(source: Dict[str, Any], section_key: str) -> float:
    text = source_text(source)
    section = _source_section_title(source)
    role = _source_role(source)
    joined = f"{section} {text}"
    score = source_score(source)

    if _source_reference_like(source):
        score -= 40.0

    if section_key == "objectif_global":
        score += 9.0 * len(_OBJECTIVE_MARKERS_RE.findall(joined))
        if "objectif" in role:
            score += 24.0
        if "contribution" in role:
            score += 15.0
        if _PROJECT_ACTION_RE.search(joined):
            score += 10.0
        # Un titre contenant « objectifs » ne suffit pas : le passage lui-même
        # doit porter une intention/action projet. Cela écarte les listes de
        # documents et métadonnées placées sous une section « Contexte et objectifs ».
        if not _OBJECTIVE_MARKERS_RE.search(text) and not _PROJECT_ACTION_RE.search(text):
            score -= 24.0
        # Un pur résultat chiffré ne doit pas devenir l'objectif du projet.
        if "resultat" in role and not _OBJECTIVE_MARKERS_RE.search(text):
            score -= 10.0

    elif section_key == "parametres_contraintes":
        score += 8.0 * min(len(_PARAMETER_RE.findall(joined)), 5)
        score += 2.0 * min(len(_QUANT_RE.findall(text)), 5)
        if "parametre" in role:
            score += 20.0
        if "methode" in role:
            score += 5.0
        if "resultat" in role and not _PARAMETER_RE.search(joined):
            score -= 12.0

    elif section_key == "demarche_detectee":
        score += 7.0 * min(len(_METHOD_RE.findall(joined)), 6)
        if "methode" in role:
            score += 20.0
        if _PROJECT_ACTION_RE.search(joined):
            score += 10.0

    elif section_key == "resultats_metriques":
        score += 8.0 * min(len(_RESULT_RE.findall(joined)), 6)
        score += 2.0 * min(len(_QUANT_RE.findall(text)), 6)
        if "resultat" in role:
            score += 24.0
        if _source_reference_like(source):
            score -= 80.0

    return score


def _all_section_sources(sections: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for key, values in (sections or {}).items():
        if str(key).startswith("_") or not isinstance(values, list):
            continue
        output.extend(item for item in values if isinstance(item, dict) and source_text(item))
    return output


def select_sources_for_section(
    sections: Dict[str, List[Dict[str, Any]]],
    config: SectionContextConfig,
) -> List[Dict[str, Any]]:
    """Sélectionne les preuves adaptées à la fonction de la section.

    Le système reste générique : aucun nom de projet, technologie, valeur ou domaine
    n'est codé en dur. Si l'objectif n'est pas rangé dans role=objectif, un fallback
    parcourt les autres preuves du projet au lieu d'afficher « aucune preuve ».
    """
    candidates: List[Dict[str, Any]] = []
    for key in config.source_keys:
        values = sections.get(key) if isinstance(sections, dict) else []
        if isinstance(values, list):
            candidates.extend(item for item in values if isinstance(item, dict) and source_text(item))

    if config.key == "objectif_global":
        # Fallback indispensable : les objectifs sont souvent formulés dans une
        # contribution, une méthode, un abstract ou une conclusion.
        if len(candidates) < 3:
            candidates.extend(_all_section_sources(sections))

    if config.key == "parametres_contraintes" and len(candidates) < 4:
        candidates.extend(_all_section_sources(sections))

    seen = set()
    ranked: List[Tuple[float, Dict[str, Any]]] = []
    for source in candidates:
        signature = _source_signature(source)
        if signature in seen:
            continue
        seen.add(signature)
        score = _section_source_score(source, config.key)
        # Pour objectif/paramètre, éviter d'injecter des preuves totalement hors fonction.
        if config.key in {"objectif_global", "parametres_contraintes"} and score < 0:
            continue
        ranked.append((score, source))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [source for _, source in ranked[: config.top_k_evidence]]


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        parsed = int(value)
        return parsed if parsed >= 0 else None
    except Exception:
        return None


def evidence_from_source(source: Dict[str, Any], evidence_id: str, excerpt_limit: int) -> Dict[str, Any]:
    meta = meta_of(source)
    rag_chunk_id = clean_text(
        meta.get("rag_chunk_id") or source.get("id") or meta.get("passage_id") or "",
        240,
    )
    return {
        "evidence_id": evidence_id,
        "rag_chunk_id": rag_chunk_id,
        "passage_id": clean_text(meta.get("passage_id") or meta.get("original_passage_id") or "", 240),
        "document_id": clean_text(meta.get("document_id") or "", 240),
        "document": source_doc(source),
        "source_path": clean_text(meta.get("source_path") or source.get("source_path") or "", 900),
        "page_number": _safe_int(meta.get("page_number") or meta.get("page")),
        "paragraph_index": _safe_int(meta.get("paragraph_index")),
        "char_start": _safe_int(meta.get("char_start") or meta.get("start_char") or meta.get("start")),
        "char_end": _safe_int(meta.get("char_end") or meta.get("end_char") or meta.get("end")),
        "section_title": clean_text(meta.get("section_title") or meta.get("section") or "", 240),
        "role": clean_text(meta.get("role") or meta.get("final_role") or source.get("role") or "", 80),
        "proof_kind": clean_text(meta.get("proof_kind") or "", 80),
        "operation_number": meta.get("operation_number"),
        "operation_group_id": clean_text(meta.get("operation_group_id") or "", 240) or None,
        "operation_function": clean_text(meta.get("operation_function") or "", 80) or None,
        "result_scope": clean_text(meta.get("result_scope") or "", 80),
        "primary_result_evidence": bool(meta.get("primary_result_evidence")),
        "reference_like": _source_reference_like(source),
        "quantitative_values": _QUANT_RE.findall(source_text(source))[:12],
        "excerpt": clean_text(source_text(source), excerpt_limit),
    }


_SECTION_INSTRUCTIONS = {
    "synthese_strategique": (
        "Rédige deux ou trois paragraphes : contexte et enjeu technique, difficulté R&D, puis portée de l'analyse. "
        "Ne transforme pas un résultat en verrou et reste prudent."
    ),
    "objectif_global": (
        "Rédige un seul paragraphe précis présentant l'objet technique du projet, l'action recherchée et la manière dont "
        "le projet prévoit d'évaluer l'atteinte de cet objectif. Ne crée jamais de seuil, cible ou critère de réussite "
        "s'il n'est pas explicitement présent dans les preuves. Si aucun seuil n'est documenté, décris simplement l'objectif "
        "et l'approche d'évaluation. L'objectif peut être formulé dans un passage objectif, contribution, méthode ou conclusion : "
        "utilise le contenu réel des preuves et ne réponds jamais qu'aucune preuve n'existe si des preuves numérotées sont fournies."
    ),
    "justification_frascati": (
        "Rédige un seul paragraphe global, continu et compréhensible par un consultant CIR. Dans ce même paragraphe, "
        "relie le score et les critères Frascati, les verrous ou incertitudes, les hypothèses, les expérimentations, "
        "les résultats et apprentissages, la nature R&D ou classique des démarches et les éléments restant à valider. "
        "Synthétise les différentes opérations sans créer une justification séparée par opération. Utilise le rattachement "
        "sémantique fourni avec chaque preuve pour ne relier une preuve à une opération que lorsque ce lien est explicite. "
        "Explique la part documentaire restante sans recalculer ni inventer de valeur. "
        "Ne récite jamais la formule de calcul. Commence par une conclusion métier sur l’éligibilité et la lisibilité : "
        "nomme concrètement le verrou technique démontré par le dossier, explique l’hypothèse ou la raison de recherche, "
        "décris la démarche réellement menée et les résultats qui la confirment ou la fragilisent. Explique ensuite pourquoi "
        "les critères Frascati sont satisfaits ou seulement partiels dans ce projet. Si l’indice laisse une part non documentée, "
        "attribue cette part aux lacunes précises du projet, par exemple une nouveauté insuffisamment comparée à l’existant, "
        "une créativité peu explicitée ou une transférabilité non démontrée. La preuve F0 autorise uniquement les nombres du "
        "score ; elle ne prouve ni le verrou, ni la démarche, ni les résultats. Le paragraphe doit citer plusieurs preuves "
        "documentaires du projet en plus de F0. Respecte strictement l’ordre : contexte, verrou, hypothèse, méthodes et "
        "outils, étapes expérimentales, résultats interprétés, apprentissage, critères Frascati acquis, critères restant "
        "à consolider, puis conclusion d’éligibilité. Pour chaque critère partiel, indique sa contribution manquante issue "
        "de F0 et la lacune documentaire concrète du projet qui l’explique. Tout résultat expérimental doit être formulé "
        "avec sa métrique, l’objet mesuré, les conditions comparées et la portée de l’observation ; aucune liste de nombres "
        "isolés. Le champ `claims` découpe ce paragraphe en affirmations consécutives afin de rattacher chaque affirmation "
        "à ses propres evidence_ids ; la concaténation des claims doit reproduire le paragraphe sans créer plusieurs blocs. "
        "Chaque nombre et chaque relation causale doivent être présents explicitement dans les preuves citées. "
        "N'utilise jamais dans le texte visible les termes internes rnd_core_partial, evidence_score, semantic_role ou "
        "des identifiants techniques. N'attribue jamais un gain à une méthode si la preuve ne formule pas elle-même "
        "cette causalité. Les preuves manquantes augmentent le risque et demandent une validation, sans transformer "
        "automatiquement une insuffisance en ingénierie classique."
    ),
    "demarche_detectee": (
        "Organise les travaux dans l'ordre logique sous forme de démarches numérotées. Une démarche correspond à une opération "
        "ou à une sous-étape cohérente d'une même opération : ne fusionne jamais des preuves portant des numéros d'opération différents. "
        "Chaque démarche doit nommer l'objectif de l'étape, la méthode réellement appliquée, les données/conditions utilisées et ce qu'elle "
        "cherche à vérifier. Préserve strictement la nature des données : une production de données synthétiques ne devient pas une acquisition "
        "de mesures réelles. Ne déduis jamais un intervalle, une plage ou un nombre total à partir d'autres nombres ; reprends seulement les valeurs "
        "explicitement écrites. Si un nombre de positions, un pas ou une plage semblent contradictoires entre preuves, expose les valeurs sans les "
        "réconcilier et demande une validation consultant. Une hypothèse peut être reconstruite à partir de plusieurs preuves de la même opération. "
        "Ne considère jamais le nombre d'étapes comme une preuve de R&D et distingue l'expérimentation R&D, le support nécessaire et l'ingénierie classique."
    ),
    "resultats_metriques": (
        "Rédige des paragraphes thématiques séparant les familles de résultats : comparaison globale, gain observé, étude d'ablation, "
        "métriques par classe/cas et limites. Ne mélange jamais deux expériences différentes dans la même conclusion causale. Utilise en priorité "
        "les preuves marquées primary_result_evidence et les scopes global_comparison, global_metric, observed_gain ou observed_metric. Une métrique "
        "par classe/cible ne doit jamais être présentée comme performance globale. Une marge théorique vers une borne (par exemple vers 100 %) n'est "
        "pas un résultat expérimental principal. Ne calcule aucun gain ni écart : il doit être explicitement écrit dans une preuve. Ignore références "
        "bibliographiques, auteurs, affiliations, titres et métadonnées. Une comparaison explicitement faite avec un travail précédent/une ancienne "
        "version est un résultat historique séparé : ne la transforme jamais en comparaison entre les méthodes du protocole courant. Si le contexte "
        "d'une valeur est ambigu, omets-la plutôt que de l'attribuer à la mauvaise expérience. Chaque valeur doit être associée à son sujet, sa métrique et sa condition."
    ),
    "parametres_contraintes": (
        "Organise les paramètres et contraintes en éléments numérotés. Pour chacun, indique d'abord sa nature : paramètre du protocole, "
        "paramètre de simulation/modèle, contrainte d'un jeu de données ou benchmark, ou limite documentaire. Ne transforme pas une contrainte "
        "propre à un jeu de référence en paramètre général du projet. Reprends les valeurs exactement telles qu'elles figurent dans les preuves. "
        "Explique une influence sur validité, robustesse, représentativité ou coût seulement si ce lien est explicitement soutenu par la preuve ; "
        "sinon indique simplement que le paramètre doit être contrôlé/interprété. Ne déduis pas qu'une densité, un nombre de configurations ou une "
        "plage 'augmente la représentativité' sans preuve explicite. Le nom du paramètre (azimut, incidence, dépression, densité, etc.) doit être "
        "explicitement présent dans le texte contextualisé de la preuve ; ne le déduis jamais du titre de section seul."
    ),
}


def _schema_for(config: SectionContextConfig) -> Dict[str, Any]:
    if config.display_mode == "numbered_items":
        return {
            "items": [
                {
                    "label": "Démarche 1" if config.key == "demarche_detectee" else "Point 1",
                    "text": "Explication complète.",
                    "evidence_ids": ["E1"],
                }
            ]
        }
    if config.key == "justification_frascati":
        # Schéma volontairement plat : il est beaucoup plus robuste en JSON mode
        # qu'un tableau paragraphs contenant lui-même claims. Le parseur reconstruit
        # ensuite le format historique attendu par le frontend.
        return {
            "text": "Un seul paragraphe global suivant la séquence métier imposée.",
            "evidence_ids": ["F0", "F1", "F2"],
            "claims": [
                {
                    "claim_kind": "contexte_verrou",
                    "text": "Contexte et verrou projet-spécifiques.",
                    "evidence_ids": ["F1"],
                },
                {
                    "claim_kind": "hypothese_demarche_resultats",
                    "text": "Hypothèse, méthodes, étapes, résultats et apprentissage.",
                    "evidence_ids": ["F2"],
                },
                {
                    "claim_kind": "frascati_conclusion",
                    "text": "Pourquoi l'indice est acquis, ce qui reste à consolider et conclusion CIR.",
                    "evidence_ids": ["F0", "F1"],
                },
            ],
        }
    return {
        "paragraphs": [
            {
                "text": "Paragraphe explicatif complet.",
                "evidence_ids": ["E1"],
            }
        ]
    }


def _style_role_for_section(section_key: str) -> str:
    return {
        "synthese_strategique": "objectif",
        "objectif_global": "objectif",
        "justification_frascati": "verrou",
        "demarche_detectee": "methode",
        "resultats_metriques": "resultat",
        "parametres_contraintes": "parametre",
    }.get(str(section_key or ""), "verrou")


def _style_context_for_section(
    config: SectionContextConfig,
    style_memory_report: Optional[Dict[str, Any]],
    max_chars: int = 1100,
) -> str:
    """Retourne des exemples de forme, explicitement séparés des preuves."""
    if _current_project_only_mode():
        return (
            "Style rédactionnel interne uniquement : français professionnel, factuel, concis, "
            "orienté consultant CIR. Aucun exemple provenant d'un autre projet n'est injecté."
        )
    report = style_memory_report if isinstance(style_memory_report, dict) else {}
    if not report.get("ok"):
        return "Aucun exemple de style disponible."
    role = _style_role_for_section(config.key)
    by_role = report.get("examples_by_role") if isinstance(report.get("examples_by_role"), dict) else {}
    examples = by_role.get(role) if isinstance(by_role.get(role), list) else []
    lines = [
        f"Rôle rédactionnel : {role}.",
        "Ces exemples indiquent seulement la forme. Ne reprendre aucun nom, chiffre, fait ou document.",
    ]
    for index, example in enumerate(examples[:2], start=1):
        if not isinstance(example, dict):
            continue
        text = clean_text(example.get("text") or example.get("content"), 380)
        if text:
            lines.append(f"Exemple de forme {index} : {text}")
    if len(lines) == 2:
        generic = clean_text(report.get("style_block"), 700)
        if generic:
            lines.append(generic)
    return clean_text("\n".join(lines), max_chars)


def _memory_v2_context(
    memory_v2_report: Optional[Dict[str, Any]],
    max_chars: int = 1000,
) -> str:
    """Contexte d'analogie uniquement ; les preuves restent celles du RAG courant."""
    if _current_project_only_mode():
        return "Memory V2 désactivée pour cette génération : seules les preuves du projet courant sont autorisées."
    report = memory_v2_report if isinstance(memory_v2_report, dict) else {}
    if not report.get("ok"):
        return "Aucun contexte de projet similaire disponible."
    block = clean_text(report.get("prompt_block"), max_chars)
    if not block:
        return "Des projets similaires ont été retrouvés, sans extrait textuel exploitable."
    return clean_text(
        "Contexte de projets similaires, non factuel :\n"
        + block
        + "\nNe jamais utiliser ce bloc comme preuve du projet courant.",
        max_chars,
    )


def _compact_eligibility_report_for_prompt(summary: Dict[str, Any]) -> Dict[str, Any]:
    report = summary.get("eligibility_evidence_report")
    if not isinstance(report, dict):
        return {}
    basis = report.get("score_basis_operation") if isinstance(report.get("score_basis_operation"), dict) else {}
    reference = report.get("reference_operation") if isinstance(report.get("reference_operation"), dict) else basis
    narrative_operation = reference or basis
    criteria = []
    for item in narrative_operation.get("criteria") or []:
        if not isinstance(item, dict):
            continue
        criteria.append({
            "criterion": item.get("criterion"),
            "label": item.get("label"),
            "status": item.get("status"),
            "contribution_to_index": item.get("contribution_to_index"),
            "remaining_gap_to_full_coverage": item.get("remaining_gap_to_full_coverage"),
            "reason": item.get("reason"),
            "question": item.get("question"),
        })
    operations = []
    for operation in report.get("operations") or []:
        if not isinstance(operation, dict):
            continue
        coherence = operation.get("causal_coherence") if isinstance(operation.get("causal_coherence"), dict) else {}
        operations.append({
            "group_id": operation.get("group_id"),
            "title": operation.get("title"),
            "operation_status": operation.get("operation_status"),
            "consultant_validation_required": operation.get("consultant_validation_required"),
            "semantic_link_quality_score": operation.get("semantic_link_quality_score"),
            "causal_coherence": {
                "score": coherence.get("score"),
                "stages_present": coherence.get("stages_present") or {},
                "linked_pairs": coherence.get("linked_pairs") or [],
            },
        })
    return {
        "score": report.get("score"),
        "documented_share": report.get("documented_share"),
        "remaining_documentary_gap": report.get("remaining_documentary_gap"),
        "score_formula": report.get("score_formula"),
        "score_basis_group_id": report.get("score_basis_group_id"),
        "reference_operation_group_id": report.get("reference_operation_group_id"),
        "attachment_policy": report.get("attachment_policy") or {},
        "hypothesis_evidence_count": len(report.get("hypothesis_reconstruction_evidence") or []),
        "prioritized_result_evidence_count": len(report.get("prioritized_result_evidence") or []),
        "score_basis_operation": {
            "title": basis.get("title"),
            "operation_status": basis.get("operation_status"),
            "causal_chain_complete": basis.get("causal_chain_complete"),
            "criteria": criteria,
        },
        "reference_operation": {
            "title": reference.get("title"),
            "operation_status": reference.get("operation_status"),
        },
        "operations": operations,
    }


def _official_frascati_evidence(
    summary: Dict[str, Any],
    max_items: int = 14,
    purpose: str = "justification_frascati",
) -> List[Dict[str, Any]]:
    """Transforme les preuves NLP officielles en preuves numérotées du prompt."""
    def percent_literal(value: Any) -> str:
        try:
            number = float(value)
        except Exception:
            return "non disponible"
        if number <= 1:
            number *= 100
        rounded = round(number, 2)
        return (f"{int(rounded)}" if rounded.is_integer() else str(rounded).replace(".", ",")) + " %"

    report = summary.get("eligibility_evidence_report")
    if not isinstance(report, dict) or not report:
        return []
    basis = report.get("score_basis_operation") if isinstance(report.get("score_basis_operation"), dict) else {}
    reference = report.get("reference_operation") if isinstance(report.get("reference_operation"), dict) else basis
    narrative_operation = reference or basis
    criteria = [item for item in (narrative_operation.get("criteria") or []) if isinstance(item, dict)]
    formula_parts = []
    status_labels = {
        "documented": "documenté",
        "partial": "partiel",
        "missing": "manquant",
        "contradictory": "contradictoire",
    }
    for item in criteria:
        formula_parts.append(
            f"{item.get('label') or item.get('criterion')}: statut {status_labels.get(str(item.get('status')), 'à vérifier')}, "
            f"contribution {item.get('contribution_to_index')} ({percent_literal(item.get('contribution_to_index'))}), "
            f"écart restant {item.get('remaining_gap_to_full_coverage')} "
            f"({percent_literal(item.get('remaining_gap_to_full_coverage'))})"
        )
    operation_status_counts: Dict[str, int] = {}
    for operation in report.get("operations") or []:
        if not isinstance(operation, dict):
            continue
        status = clean_text(operation.get("operation_status")) or "insufficient_evidence"
        operation_status_counts[status] = operation_status_counts.get(status, 0) + 1

    score_text = (
        f"Calcul déterministe NLP/Frascati. Indice de défendabilité R&D: {report.get('score')} "
        f"({percent_literal(report.get('score'))}). Part documentée: {report.get('documented_share')} "
        f"({percent_literal(report.get('documented_share'))}). Part restante: "
        f"{report.get('remaining_documentary_gap')} "
        f"({percent_literal(report.get('remaining_documentary_gap'))}). "
        f"Statut de l'opération de référence: {clean_text(reference.get('operation_status')) or 'à qualifier'}. "
        f"Périmètre des opérations: {json.dumps(operation_status_counts, ensure_ascii=False)}. "
        "Règle: cinq critères de poids égal; documenté apporte 0.2, partiel 0.1, "
        "manquant ou contradictoire 0. "
        + "; ".join(formula_parts)
    )
    evidence: List[Dict[str, Any]] = []
    if purpose == "justification_frascati":
        evidence.append({
            "evidence_id": "F0",
            "rag_chunk_id": "",
            "passage_id": "",
            "document_id": "",
            "document": "Évaluation NLP/Frascati",
            "source_path": "",
            "page_number": None,
            "paragraph_index": None,
            "char_start": None,
            "char_end": None,
            "section_title": "Règle de calcul et opération de référence",
            "role": "calculated_assessment",
            "sentence_start": None,
            "excerpt": clean_text(score_text, 1800),
            "criteria_assessment": [
                {
                    "label": item.get("label") or item.get("criterion"),
                    "status": item.get("status"),
                    "contribution_to_index": item.get("contribution_to_index"),
                    "remaining_gap_to_full_coverage": item.get("remaining_gap_to_full_coverage"),
                }
                for item in criteria
            ],
        })

    source_proofs: List[Dict[str, Any]] = []
    hypothesis_proofs = [
        proof for proof in (report.get("hypothesis_reconstruction_evidence") or [])
        if isinstance(proof, dict)
    ][:4]
    result_proofs = [
        proof for proof in (report.get("prioritized_result_evidence") or [])
        if isinstance(proof, dict)
    ][:3]
    operations = [item for item in (report.get("operations") or []) if isinstance(item, dict)]

    def operation_proofs_round_robin() -> List[Dict[str, Any]]:
        ordered = sorted(
            operations,
            key=lambda item: item.get("group_id") != report.get("reference_operation_group_id"),
        )
        operation_numbers = {
            clean_text(operation.get("group_id")): index
            for index, operation in enumerate(operations, start=1)
            if clean_text(operation.get("group_id"))
        }
        stages = ("uncertainty", "hypothesis", "experiment", "result", "learning")
        tagged: List[Dict[str, Any]] = []
        seen_ids = set()

        def append_proof(
            proof: Dict[str, Any],
            operation_index: int,
            operation_group_id: str,
            stage: str,
        ) -> None:
            signature = clean_text(proof.get("evidence_id")) or (
                clean_text(proof.get("document")),
                clean_text(proof.get("excerpt"))[:220],
            )
            if signature in seen_ids:
                return
            seen_ids.add(signature)
            tagged.append({
                **proof,
                "operation_number": operation_index,
                "operation_group_id": operation_group_id or None,
                "operation_function": stage,
            })

        def preferred_stage_proof(stage: str, proofs: Any) -> Optional[Dict[str, Any]]:
            if not isinstance(proofs, list):
                return None
            valid = [item for item in proofs if isinstance(item, dict)]
            if not valid:
                return None
            if stage == "hypothesis":
                return next((item for item in valid if item.get("hypothesis_anchor")), valid[0])
            if stage == "result":
                return next((item for item in valid if item.get("primary_result_evidence")), valid[0])
            return valid[0]

        for operation in ordered:
            operation_group_id = clean_text(operation.get("group_id"))
            operation_index = operation_numbers.get(operation_group_id, 1)
            functional = operation.get("functional_evidence") if isinstance(operation.get("functional_evidence"), dict) else {}
            for stage in stages:
                proofs = functional.get(stage) or []
                preferred = preferred_stage_proof(stage, proofs)
                if preferred is not None:
                    append_proof(preferred, operation_index, operation_group_id, stage)
                    break
        for stage in stages:
            for operation in ordered:
                operation_group_id = clean_text(operation.get("group_id"))
                operation_index = operation_numbers.get(operation_group_id, 1)
                functional = operation.get("functional_evidence") if isinstance(operation.get("functional_evidence"), dict) else {}
                proofs = functional.get(stage) or []
                preferred = preferred_stage_proof(stage, proofs)
                if preferred is not None:
                    append_proof(preferred, operation_index, operation_group_id, stage)
        return tagged

    if purpose == "resultats_metriques":
        source_proofs.extend(result_proofs)
    elif purpose == "demarche_detectee":
        source_proofs.extend(operation_proofs_round_robin())
    else:
        # La conclusion d'éligibilité raconte UNE opération de référence. Les
        # preuves détaillées des autres opérations ne sont pas injectées dans le
        # même prompt, afin d'éviter les fuites de résultats et les paragraphes
        # concaténés. Leur statut global reste disponible dans F0.
        operation_proofs = operation_proofs_round_robin()
        reference_group_id = clean_text(reference.get("group_id"))
        source_proofs.extend([
            proof for proof in operation_proofs
            if clean_text(proof.get("operation_group_id")) == reference_group_id
        ])
        source_proofs.extend([
            proof for proof in hypothesis_proofs
            if not reference_group_id
            or clean_text((proof.get("semantic_link") or {}).get("operation_id")) in {"", reference_group_id}
        ])
        source_proofs.extend([
            proof for proof in result_proofs
            if not reference_group_id
            or clean_text((proof.get("semantic_link") or {}).get("operation_id")) in {"", reference_group_id}
        ])
        for criterion in criteria:
            proofs = criterion.get("evidence") or []
            if isinstance(proofs, list):
                source_proofs.extend(proof for proof in proofs[:2] if isinstance(proof, dict))

    seen = set()
    document_index = 1
    for proof in source_proofs:
        original_id = clean_text(proof.get("evidence_id"), 240)
        excerpt = clean_text(proof.get("excerpt"), 700)
        signature = original_id or (clean_text(proof.get("document")), excerpt[:220])
        if not excerpt or signature in seen:
            continue
        seen.add(signature)
        generated_id = f"F{document_index}"
        document_index += 1
        evidence.append({
            "evidence_id": generated_id,
            "rag_chunk_id": original_id,
            "passage_id": original_id,
            "document_id": proof.get("document_id") or "",
            "document": clean_text(proof.get("document") or proof.get("document_name"), 260),
            "document_name": clean_text(proof.get("document_name") or proof.get("document"), 260),
            "source_path": clean_text(proof.get("source_path"), 900),
            "page_number": _safe_int(proof.get("page_number")),
            "paragraph_index": _safe_int(proof.get("paragraph_index")),
            "char_start": _safe_int(proof.get("char_start") or proof.get("sentence_start")),
            "char_end": _safe_int(proof.get("char_end")),
            "sentence_start": _safe_int(proof.get("sentence_start")),
            "section_title": clean_text(proof.get("section_title"), 240),
            "section_path": clean_text(proof.get("section_path"), 500),
            "role": clean_text(
                (
                    f"opération {proof.get('operation_number')} — {proof.get('operation_function')}"
                    if proof.get("operation_number") and proof.get("operation_function")
                    else proof.get("role")
                ),
                80,
            ),
            "operation_number": proof.get("operation_number"),
            "operation_group_id": clean_text(proof.get("operation_group_id"), 240) or None,
            "operation_function": clean_text(proof.get("operation_function"), 80) or None,
            "summary_fr": clean_text(proof.get("summary_fr"), 700),
            "proof_kind": clean_text(proof.get("proof_kind"), 80),
            "result_scope": clean_text(proof.get("result_scope"), 80),
            "quantitative_values": list(proof.get("quantitative_values") or []),
            "reference_like": bool(proof.get("reference_like")),
            "primary_result_evidence": bool(proof.get("primary_result_evidence")),
            "hypothesis_explicit": proof.get("hypothesis_explicit"),
            "hypothesis_anchor": proof.get("hypothesis_anchor"),
            "source_text_original": clean_text(proof.get("source_text_original") or excerpt, 1200),
            "source_field": clean_text(proof.get("source_field"), 80),
            "source_is_original": proof.get("source_is_original"),
            "highlight_coordinates": proof.get("highlight_coordinates"),
            "semantic_link": proof.get("semantic_link") or {},
            "justification_bridge_fr": clean_text(proof.get("justification_bridge_fr"), 900),
            "excerpt": excerpt,
        })
        if len(evidence) >= max_items:
            break
    return evidence


def _compact_frascati_for_prompt(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Réduit le résumé Frascati afin de toujours garder de la place aux preuves."""
    value = summary if isinstance(summary, dict) else {}
    groups = []
    for raw in (value.get("main_group_assessments") or value.get("group_assessments") or [])[:12]:
        if not isinstance(raw, dict):
            continue
        groups.append({
            "group_id": raw.get("group_id"),
            "eligibility_score": raw.get("eligibility_score"),
            "risk_level": raw.get("risk_level"),
            "interpretation": raw.get("interpretation"),
            "technical_scope": raw.get("technical_scope"),
            "operation_title": raw.get("operation_title"),
            "eligibility_recommendation": raw.get("eligibility_recommendation"),
            "operation_status": (
                (raw.get("demarche_legibility") or {}).get("operation_status")
                if isinstance(raw.get("demarche_legibility"), dict)
                else None
            ),
        })
    audit = value.get("rag_audit") if isinstance(value.get("rag_audit"), dict) else {}
    demarche = value.get("demarche_legibility") if isinstance(value.get("demarche_legibility"), dict) else {}
    return {
        "average_frascati_score": value.get("average_frascati_score"),
        "eligibility_assessment_score": value.get("eligibility_assessment_score"),
        "rnd_defensibility_index": value.get("rnd_defensibility_index"),
        "documentary_coverage": value.get("documentary_coverage"),
        "portfolio_criteria_coverage": value.get("portfolio_criteria_coverage"),
        "remaining_documentary_gap": value.get("remaining_documentary_gap"),
        "score_formula": value.get("score_formula"),
        "score_basis_group_id": value.get("score_basis_group_id"),
        "eligibility_recommendation": value.get("eligibility_recommendation"),
        "recommendation_label": value.get("recommendation_label"),
        "scores_count": value.get("scores_count"),
        "main_groups_scores_count": value.get("main_groups_scores_count"),
        "main_groups_average_frascati_score": value.get("main_groups_average_frascati_score"),
        "risk_level": value.get("risk_level"),
        "score_source": value.get("score_source"),
        "decisions_count": value.get("decisions_count"),
        "candidate_levels_count": value.get("candidate_levels_count"),
        "group_assessments": groups,
        "eligibility_evidence_report": _compact_eligibility_report_for_prompt(value),
        "demarche_legibility": {
            key: demarche.get(key)
            for key in (
                "analysis_unit",
                "direct_rnd_rule",
                "project_status",
                "operation_status",
                "operations_count",
                "operation_count",
                "rnd_core_defendable_operations_count",
                "rnd_core_partial_operations_count",
                "classical_engineering_operations_count",
                "insufficient_evidence_operations_count",
                "all_operations_classical_engineering",
                "label",
                "readability_score",
                "documentary_confidence",
                "activities_count",
                "direct_rnd_activities_count",
                "necessary_rnd_support_activities_count",
                "classical_engineering_activities_count",
                "insufficient_evidence_activities_count",
                "method_steps_count",
                "research_justified_steps_count",
                "routine_engineering_steps_count",
                "unexplained_steps_count",
                "redundant_steps_count",
                "direct_final_solution_risk",
                "eligibility_impact",
                "risk_adjustment",
                "llm_review_recommended",
                "llm_review_reasons",
                "questions_to_ask",
            )
            if key in demarche
        },
        "rag_audit": {
            "scores_count": audit.get("scores_count"),
            "average_frascati_score": audit.get("average_frascati_score"),
            "consistent_with_nlp": audit.get("consistent_with_nlp"),
            "score_rows": audit.get("score_rows") or [],
        },
    }


def build_section_context(
    config: SectionContextConfig,
    sections: Dict[str, List[Dict[str, Any]]],
    frascati_summary: Dict[str, Any],
    style_memory_report: Optional[Dict[str, Any]] = None,
    memory_v2_report: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    sources = select_sources_for_section(sections, config)
    evidence: List[Dict[str, Any]] = (
        _official_frascati_evidence(
            frascati_summary,
            max_items=config.top_k_evidence,
            purpose=config.key,
        )
        if config.key in {"justification_frascati", "demarche_detectee", "resultats_metriques"}
        else []
    )

    base = {
        "section": config.title,
        "instruction": _SECTION_INSTRUCTIONS[config.key],
        "format": _schema_for(config),
        "frascati": _compact_frascati_for_prompt(frascati_summary)
        if config.key in {"justification_frascati", "demarche_detectee"}
        else {},
    }
    base_prompt = f"""
Tu es EnnoDiagnostic, agent d'analyse CIR. Rédige uniquement la section « {config.title} ».

Règles absolues :
- Utilise seulement les preuves numérotées fournies.
- MODE PROJET COURANT : n'utilise aucun contenu provenant d'un autre projet, d'un exemple de style, de Memory V2 ou d'un CIR précédent pour décrire les faits du projet courant.
- Les seules informations techniques autorisées sont celles des PREUVES numérotées ci-dessous, issues du projet courant.
- Si une information n'apparaît pas dans ces PREUVES, ne la complète pas par analogie : indique qu'elle est insuffisamment documentée.
- N'invente aucun fait, résultat, paramètre ou protocole.
- Chaque paragraphe ou élément doit citer au moins un `evidence_id` autorisé.
- Pour la justification Frascati, une affirmation = une idée vérifiable. Ne répète jamais
  la même comparaison, le même résultat ou la même conclusion dans deux paragraphes.
- Pour la justification Frascati, retourne exactement un paragraphe et découpe-le en
  `claims` consécutifs couvrant : contexte, verrou, hypothèse, méthodes/outils, étapes
  expérimentales, résultats interprétés, apprentissage, critères acquis, critères à
  consolider et conclusion. Chaque claim porte ses propres `evidence_ids`, et le champ
  `text` du paragraphe est la concaténation fidèle de ces claims.
- Tout nombre relatif au projet doit apparaître exactement dans au moins une preuve citée.
  Ne calcule aucun écart, gain, moyenne ou pourcentage à partir de deux autres valeurs.
    - Les seuls nombres calculés autorisés sont ceux de la preuve F0, issue du calcul déterministe
      NLP/Frascati. Ils décrivent l'indice et sa décomposition, pas un résultat expérimental.
    - Pour la justification Frascati, F0 ne suffit jamais : le paragraphe doit utiliser les preuves
      documentaires du projet pour nommer le verrou, la démarche et les résultats. N’écris jamais
      « Calcul officiel NLP/Frascati » dans le texte visible et ne déroule pas la formule critère par critère.
- N'écris « dû à », « grâce à », « entraîne », « explique », « conduit à », « permet » ou
  une autre causalité que si la relation causale est formulée explicitement dans la preuve citée.
- Ne transforme jamais un résultat observé en objectif initial, cible, seuil ou critère
  de réussite. Une valeur ne peut être présentée comme cible que si la preuve utilise
  explicitement une formulation de cible, exigence, seuil, attendu ou objectif.
- Si une preuve contient une comparaison numérique contradictoire avec les mots
  « inférieur » ou « supérieur », signale la contradiction et demande une validation
  consultant ; ne la résous pas et ne la répète pas comme une conclusion certaine.
- Conserve les expressions techniques comme des unités lexicales : ne transforme
  jamais « débit » en « bit », « diamètre » ou un terme ressemblant.
- Préserve exactement la nature des objets, composants et phénomènes techniques
  décrits dans les preuves. N'introduis aucun autre secteur, équipement, bâtiment,
  lieu ou architecture absent des preuves.
- Si un terme est ambigu, conserve l'expression technique exacte de la preuve et
  n'en déduis jamais un autre sens. Par exemple, un étage de compression reste un
  étage de compression et ne devient pas un étage de bâtiment.
- Une adresse postale, un nom de société ou un lieu de document ne décrit jamais
  le système technique, sauf si une preuve l'affirme explicitement.
- Préfère les formulations concrètes fondées sur l'objet, le phénomène et les
  contraintes chiffrées disponibles aux expressions vagues comme « conditions
  optimales » ou « amélioration des performances ».
    - Ne cite aucun nom de fichier dans le texte visible.
    - Tout le texte visible doit être rédigé en français. Si une preuve est en anglais,
      reformule ou traduis fidèlement son contenu sans afficher le passage anglais comme justification principale.
    - N'emploie jamais dans le texte visible les codes internes rnd_core_defendable,
      rnd_core_partial, insufficient_evidence, classical_engineering, evidence_score ou semantic_role.
- N'écris jamais « Source », « Indice source », « Dans le document » ou une référence technique dans le texte.
- N'écris jamais les identifiants internes E1, E2, G1.S1 ou similaires dans le texte visible.
- Place les références uniquement dans `evidence_ids`.
- Aucun Markdown, aucun titre et aucun tableau.
- Retourne uniquement le JSON demandé.
- Entre {config.min_items} et {config.max_items} éléments.

Instruction métier :
{_SECTION_INSTRUCTIONS[config.key]}

Contexte fixe :
{json.dumps(base, ensure_ascii=False, indent=2)}

STYLE — FORME UNIQUEMENT :
{_style_context_for_section(config, style_memory_report)}

MEMORY V2 — ANALOGIE UNIQUEMENT :
{_memory_v2_context(memory_v2_report)}

PREUVES :
{{evidence_json}}
""".strip()

    # Les petites sections n'ont pas besoin du très long contrat de la conclusion
    # Frascati. Un prompt compact réserve réellement le budget aux PREUVES ;
    # auparavant l'objectif pouvait partir avec zéro preuve parce que les règles
    # consommaient presque tout max_input_tokens.
    if config.key != "justification_frascati":
        base_prompt = f"""
Tu es EnnoDiagnostic. Rédige uniquement « {config.title} » en français.

CONTRAT :
- Utilise exclusivement les PREUVES numérotées du projet courant.
- Aucun autre projet, Memory V2, CIR précédent ou exemple de style ne peut fournir un fait.
- N'invente ni objet, ni méthode, ni paramètre, ni résultat, ni causalité, ni chiffre.
- Chaque paragraphe/élément doit citer au moins un evidence_id autorisé.
- Un nombre visible doit exister tel quel dans la preuve citée ; ne calcule aucun écart ou gain.
- Ne transforme pas un résultat en objectif ni une contrainte de benchmark en paramètre général du projet.
- Pour une preuve fragmentée, utilise son texte contextualisé ; le titre de section sert de localisation, pas de preuve sémantique.
- Ne cite aucun nom de fichier ni identifiant E/F/G dans le texte visible.
- Retourne uniquement le JSON correspondant au schéma.

Instruction métier :
{_SECTION_INSTRUCTIONS[config.key]}

Schéma :
{json.dumps(_schema_for(config), ensure_ascii=False)}

PREUVES :
{{evidence_json}}
""".strip()

    # Pour la justification, la démarche et les résultats, les preuves officielles
    # ont déjà été classées par fonction et rattachées aux opérations. Si elles sont
    # disponibles, ne pas réinjecter ensuite des chunks RAG génériques : c'était la
    # principale source de mélange entre opérations, littérature et résultats locaux.
    official_only_sections = {"justification_frascati", "demarche_detectee", "resultats_metriques"}
    sources_to_append = [] if (config.key in official_only_sections and evidence) else sources

    for index, source in enumerate(sources_to_append, start=1):
        item = evidence_from_source(source, f"E{index}", config.max_chars_per_evidence)
        candidate = evidence + [item]
        prompt_candidate = base_prompt.replace(
            "{evidence_json}",
            json.dumps(
                [
                    {
                        "evidence_id": ev["evidence_id"],
                        "role": ev.get("role"),
                        "section_title": ev.get("section_title"),
                        "proof_kind": ev.get("proof_kind"),
                        "operation_number": ev.get("operation_number"),
                        "operation_group_id": ev.get("operation_group_id"),
                        "operation_function": ev.get("operation_function"),
                        "result_scope": ev.get("result_scope"),
                        "primary_result_evidence": ev.get("primary_result_evidence"),
                        "reference_like": ev.get("reference_like"),
                        "quantitative_values": ev.get("quantitative_values") or [],
                        "rattachement_operation": ev.get("justification_bridge_fr") or None,
                        "text": ev["excerpt"],
                    }
                    for ev in candidate
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        if estimate_tokens(prompt_candidate) > config.max_input_tokens:
            break
        evidence = candidate

    prompt = base_prompt.replace(
        "{evidence_json}",
        json.dumps(
            [
                {
                    "evidence_id": ev["evidence_id"],
                    "role": ev.get("role"),
                    "section_title": ev.get("section_title"),
                    "proof_kind": ev.get("proof_kind"),
                    "operation_number": ev.get("operation_number"),
                    "operation_group_id": ev.get("operation_group_id"),
                    "operation_function": ev.get("operation_function"),
                    "result_scope": ev.get("result_scope"),
                    "primary_result_evidence": ev.get("primary_result_evidence"),
                    "reference_like": ev.get("reference_like"),
                    "quantitative_values": ev.get("quantitative_values") or [],
                    "rattachement_operation": ev.get("justification_bridge_fr") or None,
                    "text": ev["excerpt"],
                }
                for ev in evidence
            ],
            ensure_ascii=False,
            indent=2,
        ),
    )
    return prompt, evidence


def _valid_evidence_ids(values: Any, allowed: Iterable[str]) -> List[str]:
    allowed_set = set(allowed)
    if not isinstance(values, list):
        return []
    output: List[str] = []
    for value in values:
        item = clean_text(value, 30)
        if item in allowed_set and item not in output:
            output.append(item)
    return output


def _infer_evidence_ids_for_text(
    text: Any,
    evidence: Sequence[Dict[str, Any]],
    max_ids: int = 2,
) -> List[str]:
    """Répare un evidence_id omis par le LLM sans deviner le contenu.

    On rattache uniquement si le texte généré partage des termes techniques ou
    des valeurs numériques avec la preuve. Si aucun recouvrement crédible n'est
    trouvé, on laisse la liste vide et le garde signale le problème.
    """
    norm = _grounding_norm(text)
    tokens = {tok for tok in norm.split() if len(tok) >= 4 and tok not in _PROJECT_SCOPE_STOPWORDS}
    numbers = set(_number_tokens(text)) if '_number_tokens' in globals() else set()
    ranked: List[Tuple[float, str]] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        eid = clean_text(item.get("evidence_id"), 30)
        if not eid:
            continue
        excerpt = str(item.get("excerpt") or "")
        ev_tokens = {tok for tok in _grounding_norm(excerpt).split() if len(tok) >= 4 and tok not in _PROJECT_SCOPE_STOPWORDS}
        if not ev_tokens:
            continue
        common = tokens & ev_tokens
        overlap = len(common) / max(1, min(len(tokens), 30))
        ev_numbers = set(_number_tokens(excerpt)) if '_number_tokens' in globals() else set()
        number_bonus = 0.35 if numbers and numbers.issubset(ev_numbers) else (0.12 if numbers & ev_numbers else 0.0)
        operation_bonus = 0.08 if item.get("operation_group_id") else 0.0
        score = overlap + number_bonus + operation_bonus
        if score >= 0.10 or (numbers and numbers & ev_numbers):
            ranked.append((score, eid))
    ranked.sort(reverse=True)
    return [eid for _, eid in ranked[:max_ids]]


def _label_for(config: SectionContextConfig, index: int, proposed: Any) -> str:
    if config.key == "demarche_detectee":
        return f"Démarche {index}"
    if config.key == "parametres_contraintes":
        return f"Paramètre ou contrainte {index}"
    label = sanitize_generated_text(proposed, 100)
    return label or f"Point {index}"


def _grounding_norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_PROJECT_SCOPE_STOPWORDS = {
    "avec", "dans", "pour", "sans", "sous", "entre", "ainsi", "cette", "ces", "des", "les", "une", "dans",
    "projet", "objectif", "technique", "techniques", "methode", "methodes", "resultat", "resultats", "donnees",
    "modele", "modeles", "analyse", "evaluation", "performance", "performances", "systeme", "systemes", "approche",
    "approches", "utilise", "utilisant", "permet", "permettre", "afin", "notamment", "plus", "moins", "etre",
    "sont", "vise", "recherche", "developpement", "documente", "documentee", "validation", "consultant", "cir",
}


def _technical_tokens(value: Any) -> List[str]:
    """Extrait les identifiants techniques utiles au contrôle anti-contamination.

    Les identifiants de preuve E1/F2/G1.S3 sont ignorés : ce sont des marqueurs
    internes, pas des technologies du projet.
    """
    raw = str(value or "")
    tokens: List[str] = []
    for token in re.findall(r"\b[A-Z][A-Z0-9_-]{1,}(?:s)?\b", raw):
        if re.fullmatch(r"(?:E|F)\d+|G\d+\.S\d+", token, flags=re.I):
            continue
        if len(token) >= 2 and token not in tokens:
            tokens.append(token)
    for token in re.findall(r"\b[A-Z][a-z]+[A-Z][A-Za-z0-9_-]*\b|\b[A-Z][a-z]{2,}[0-9]+\b", raw):
        if re.fullmatch(r"(?:E|F)\d+|G\d+\.S\d+", token, flags=re.I):
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def _project_scope_text_from_sections(
    sections: Dict[str, List[Dict[str, Any]]],
    max_chars: int = 80000,
) -> str:
    """Corpus lexical du projet courant utilisé uniquement comme garde anti-fuite.

    Il n'est jamais exposé comme preuve. Les affirmations restent contrôlées
    contre les evidence_ids cités ; ce corpus sert seulement à savoir qu'un
    acronyme/outil appartient bien au projet courant.
    """
    parts: List[str] = []
    total = 0
    seen = set()
    for key, values in (sections or {}).items():
        if str(key).startswith("_") or not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            text = source_text(item)
            if not text:
                continue
            sig = _grounding_norm(text[:500])
            if not sig or sig in seen:
                continue
            seen.add(sig)
            remaining = max_chars - total
            if remaining <= 0:
                return " ".join(parts)
            chunk = text[:remaining]
            parts.append(chunk)
            total += len(chunk)
    return " ".join(parts)


def _current_project_scope_errors(
    body: Any,
    evidence: Sequence[Dict[str, Any]],
    section_key: str,
    project_scope_text: str = "",
) -> List[str]:
    """Empêche une section d'introduire des technologies d'un autre projet.

    Important : le contrôle anti-contamination compare les termes au corpus du
    PROJET COURANT, pas seulement aux 2-8 preuves sélectionnées pour la section.
    Cela évite les faux rejets du type ATR/SER absents du petit sous-ensemble de
    preuves alors qu'ils sont bien présents ailleurs dans le dossier courant.
    """
    if not _current_project_only_mode():
        return []
    text = clean_text(body, 7000)
    if not text:
        return []
    evidence_text = " ".join(
        str(item.get("excerpt") or item.get("text") or "")
        for item in evidence
        if isinstance(item, dict)
    )
    scope_text = clean_text(project_scope_text, 90000) or evidence_text
    norm_scope = " " + _grounding_norm(scope_text) + " "
    errors: List[str] = []

    visible_text = _INTERNAL_EVIDENCE_TOKEN_RE.sub(" ", text)
    missing_technical = []
    for token in _technical_tokens(visible_text):
        normalized = _grounding_norm(token)
        if normalized and not re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", norm_scope):
            missing_technical.append(token)
    if missing_technical:
        errors.append(
            "identifiants techniques absents du projet courant : "
            + ", ".join(sorted(set(missing_technical)))
        )

    if section_key in {"objectif_global", "synthese_strategique"}:
        generated_tokens = {
            tok for tok in _grounding_norm(visible_text).split()
            if len(tok) >= 5 and tok not in _PROJECT_SCOPE_STOPWORDS
        }
        scope_tokens = {
            tok for tok in _grounding_norm(scope_text).split()
            if len(tok) >= 5 and tok not in _PROJECT_SCOPE_STOPWORDS
        }
        if generated_tokens:
            overlap = len(generated_tokens & scope_tokens) / max(1, len(generated_tokens))
            if overlap < 0.16:
                errors.append(
                    f"faible ancrage dans le projet courant (recouvrement lexical={overlap:.2f})"
                )
    return errors

def _unsupported_domain_terms(body: Any, evidence: Sequence[Dict[str, Any]]) -> List[str]:
    """Détecte un changement de domaine absent des preuves sélectionnées."""
    generated = f" {_grounding_norm(body)} "
    source = " " + _grounding_norm(" ".join(
        str(item.get("excerpt") or item.get("text") or "")
        for item in evidence
        if isinstance(item, dict)
    )) + " "
    unsupported: List[str] = []
    for term in sorted(DOMAIN_SHIFT_TERMS):
        normalized = _grounding_norm(term)
        if not normalized:
            continue
        pattern = rf"(?<![a-z0-9]){re.escape(normalized)}(?:s)?(?![a-z0-9])"
        if re.search(pattern, generated) and not re.search(pattern, source):
            unsupported.append(term)
    return unsupported


def _evidence_grounding_errors(
    paragraphs: Sequence[Dict[str, Any]],
    items: Sequence[Dict[str, Any]],
    evidence: Sequence[Dict[str, Any]],
) -> List[str]:
    if not evidence:
        return []
    errors: List[str] = []
    for index, paragraph in enumerate(paragraphs, start=1):
        if paragraph.get("text") and not paragraph.get("evidence_ids"):
            errors.append(f"paragraphe {index} sans evidence_id")
    for index, item in enumerate(items, start=1):
        if item.get("text") and not item.get("evidence_ids"):
            errors.append(f"élément {index} sans evidence_id")
    return errors


_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])[-+]?\d+(?:[.,]\d+)?(?:\s*%)?")
# Le garde causal ne doit viser que les ATTRIBUTIONS causales fortes.
# Les comparaisons ("contre", "écart", "gain") et les formulations
# procédurales ("permet de tester", "conduit l'équipe à comparer") ne sont
# pas des causalités en elles-mêmes et ne doivent pas faire rejeter une
# reformulation fidèle. Les nombres restent contrôlés séparément.
_STRONG_CAUSAL_RE = re.compile(
    r"\b(?:du(?:e|es)? (?:a|au|aux)|grace (?:a|au|aux)|en raison (?:de|du|des)|parce que|"
    r"cause(?:r|e|ee|es)?|provoque(?:r|e|ee|es)?|entraine(?:r|e|ee|es)?|"
    r"explique(?:r|e|ee|es)?|responsable de|attribue(?:r|e|ee|es)? (?:a|au|aux)|"
    r"due to|because|thanks to|cause(?:s|d)?|result(?:s|ed)? in|is responsible for|"
    r"attribut(?:e|ed|es) to)\b",
    flags=re.I,
)


def _number_tokens(value: Any) -> List[str]:
    output: List[str] = []
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    for match in _NUMBER_RE.finditer(text):
        token = match.group(0).replace(" ", "").replace(",", ".").replace("%", "")
        try:
            normalized = f"{float(token):.10f}".rstrip("0").rstrip(".")
        except Exception:
            normalized = token.lstrip("+")
        if normalized not in output:
            output.append(normalized)
    return output


def _unit_evidence(
    unit: Dict[str, Any],
    evidence_by_id: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    return [
        evidence_by_id[evidence_id]
        for evidence_id in (unit.get("evidence_ids") or [])
        if evidence_id in evidence_by_id
    ]


def _strict_claim_grounding_errors(
    paragraphs: Sequence[Dict[str, Any]],
    items: Sequence[Dict[str, Any]],
    evidence: Sequence[Dict[str, Any]],
    section_key: str,
) -> List[str]:
    """Contrôle factuel strict sans bloquer les reformulations légitimes.

    - Les nombres doivent être présents dans les preuves citées.
    - Une causalité FORTE doit être explicitement présente dans au moins une
      preuve citée.
    - Les claims techniques doivent citer au moins une preuve documentaire.
    - Les claims déterministes de score/Frascati peuvent s'appuyer sur F0 seul.

    Cette distinction évite que le garde-fou rejette une bonne conclusion parce
    qu'un claim "70 % / 30 %" ne cite naturellement que le calcul déterministe.
    """
    if section_key not in {"justification_frascati", "resultats_metriques"}:
        return []

    evidence_by_id = {
        str(item.get("evidence_id")): item
        for item in evidence
        if isinstance(item, dict)
    }
    errors: List[str] = []
    units = [*paragraphs, *items]

    assessment_kinds = {
        "frascati", "score", "indice", "defendabilite", "eligibilite",
        "criteres_acquis", "criteres_a_consolider", "criteres_partiels",
        "part_documentee", "part_restante", "documentary_gap",
        "conclusion", "conclusion_eligibilite", "niveau_defendabilite",
    }

    for index, unit in enumerate(units, start=1):
        text = str(unit.get("text") or "")
        cited = _unit_evidence(unit, evidence_by_id)
        cited_text = " ".join(str(item.get("excerpt") or "") for item in cited)

        # 1) Aucun nombre inventé / recalculé.
        cited_numbers = set(_number_tokens(cited_text))
        unsupported_numbers = [
            number for number in _number_tokens(text) if number not in cited_numbers
        ]
        if unsupported_numbers:
            errors.append(
                f"affirmation {index}: nombres absents des preuves citées: "
                + ", ".join(unsupported_numbers)
            )

        # 2) Causalité forte uniquement. On ne bloque plus une simple comparaison
        # ou une phrase de liaison procédurale.
        normalized_text = _grounding_norm(text)
        if _STRONG_CAUSAL_RE.search(normalized_text):
            causal_sources = [
                item for item in cited
                if _STRONG_CAUSAL_RE.search(_grounding_norm(item.get("excerpt") or ""))
            ]
            if not causal_sources:
                errors.append(
                    f"affirmation {index}: attribution causale forte absente des preuves citées"
                )

        if section_key == "justification_frascati":
            claim_kind = _grounding_norm(unit.get("claim_kind") or "")
            documentary_ids = [
                str(item.get("evidence_id"))
                for item in cited
                if str(item.get("evidence_id")) != "F0"
            ]
            has_f0 = any(str(item.get("evidence_id")) == "F0" for item in cited)

            # Score, statut des critères et conclusion d'éligibilité peuvent être
            # prouvés par F0. Les claims techniques, eux, exigent une source projet.
            is_assessment_claim = claim_kind in assessment_kinds or any(
                marker in claim_kind
                for marker in ("frascati", "critere", "score", "indice", "conclusion")
            )
            if is_assessment_claim:
                if not cited:
                    errors.append(f"affirmation {index}: aucune preuve citée")
                elif not has_f0 and not documentary_ids:
                    errors.append(f"affirmation {index}: preuve d'évaluation absente")
            elif not documentary_ids:
                errors.append(
                    f"affirmation {index}: le récit technique doit citer au moins une preuve documentaire du projet"
                )

    return errors


def _repetition_errors(
    paragraphs: Sequence[Dict[str, Any]],
    items: Sequence[Dict[str, Any]],
) -> List[str]:
    units = [*paragraphs, *items]
    token_sets = []
    for unit in units:
        tokens = {
            token for token in _grounding_norm(unit.get("text") or "").split()
            if len(token) >= 4
        }
        token_sets.append(tokens)
    errors: List[str] = []
    for right in range(1, len(token_sets)):
        for left in range(right):
            union = token_sets[left] | token_sets[right]
            if len(union) < 8:
                continue
            similarity = len(token_sets[left] & token_sets[right]) / len(union)
            if similarity >= 0.72:
                errors.append(
                    f"affirmations {left + 1} et {right + 1} répétitives"
                )
    return errors


def _attach_proof_quotes(
    units: Sequence[Dict[str, Any]],
    evidence: Sequence[Dict[str, Any]],
) -> None:
    evidence_by_id = {
        str(item.get("evidence_id")): item for item in evidence if isinstance(item, dict)
    }
    for unit in units:
        unit["proofs"] = [
            {
                "evidence_id": item.get("evidence_id"),
                "passage_id": item.get("passage_id"),
                "document_id": item.get("document_id"),
                "document": item.get("document"),
                "document_name": item.get("document_name") or item.get("document"),
                "source_path": item.get("source_path"),
                "page_number": item.get("page_number"),
                "paragraph_index": item.get("paragraph_index"),
                "char_start": item.get("char_start"),
                "char_end": item.get("char_end"),
                "section_title": item.get("section_title"),
                "section_path": item.get("section_path"),
                "sentence_start": item.get("sentence_start") or item.get("char_start"),
                "role": item.get("role"),
                "summary_fr": item.get("summary_fr"),
                "proof_kind": item.get("proof_kind"),
                "result_scope": item.get("result_scope"),
                "quantitative_values": item.get("quantitative_values") or [],
                "reference_like": item.get("reference_like"),
                "primary_result_evidence": item.get("primary_result_evidence"),
                "hypothesis_explicit": item.get("hypothesis_explicit"),
                "hypothesis_anchor": item.get("hypothesis_anchor"),
                "source_text_original": item.get("source_text_original") or item.get("excerpt"),
                "source_field": item.get("source_field"),
                "source_is_original": item.get("source_is_original"),
                "highlight_coordinates": item.get("highlight_coordinates"),
                "semantic_link": item.get("semantic_link") or {},
                "justification_bridge_fr": item.get("justification_bridge_fr"),
                "excerpt": item.get("excerpt"),
                "proof_type": (
                    "calculation_rule" if str(item.get("evidence_id")) == "F0" else "documentary_source"
                ),
            }
            for item in _unit_evidence(unit, evidence_by_id)
        ]


def _comparison_consistency_errors(body: Any) -> List[str]:
    text = _grounding_norm(body)
    errors: List[str] = []
    pattern = re.compile(
        r"(\d+(?:[.,]\d+)?)\s*(?:°?c|bar|m3/h|%|kg|db)?\s+contre\s+"
        r"(\d+(?:[.,]\d+)?)\s*(?:°?c|bar|m3/h|%|kg|db)?",
        flags=re.I,
    )
    for match in pattern.finditer(text):
        try:
            first = float(match.group(1).replace(",", "."))
            second = float(match.group(2).replace(",", "."))
        except Exception:
            continue
        context = text[max(0, match.start() - 190): min(len(text), match.end() + 80)]
        if re.search(r"\binferieur(?:e|es|s)?\b", context) and first >= second:
            errors.append(
                f"comparaison contradictoire : {first} est présenté comme inférieur à {second}"
            )
        if re.search(r"\bsuperieur(?:e|es|s)?\b", context) and first <= second:
            errors.append(
                f"comparaison contradictoire : {first} est présenté comme supérieur à {second}"
            )
    return errors


def _unsupported_target_errors(
    body: Any,
    evidence: Sequence[Dict[str, Any]],
    section_key: str,
) -> List[str]:
    if section_key != "objectif_global":
        return []
    generated = _grounding_norm(body)
    source = _grounding_norm(" ".join(
        str(item.get("excerpt") or item.get("text") or "")
        for item in evidence if isinstance(item, dict)
    ))
    target_pattern = r"\b(?:critere de reussite|cible|seuil|minimum|au moins|attendu|exigence)\b"
    if re.search(target_pattern, generated) and not re.search(target_pattern, source):
        return ["objectif transformé en cible ou critère absent des preuves"]
    return []


def _truncated_term_errors(body: Any, evidence: Sequence[Dict[str, Any]]) -> List[str]:
    """Ancien contrôle désactivé comme bloqueur.

    L'heuristique suffixe produisait des faux positifs sur des mots français
    ordinaires ("des", "les", "ces", "porte", "très"). La
    fidélité factuelle est déjà contrôlée par les evidence_ids, les nombres et
    les preuves citées.
    """
    return []


def _eligibility_narrative_errors(
    body: Any,
    paragraphs: Sequence[Dict[str, Any]],
    evidence: Sequence[Dict[str, Any]],
) -> List[str]:
    """Empêche le retour à une formule de score sans récit technique explicatif."""
    text = clean_text(body, 5200)
    normalized = _grounding_norm(text)
    errors: List[str] = []
    if len(paragraphs) != 1:
        errors.append("la conclusion d’éligibilité doit former un seul paragraphe")
    if re.search(r"calcul officiel nlp.?frascati|regle\s*:\s*cinq criteres|documente apporte 0[.,]2", normalized):
        errors.append("la formule interne du calcul ne doit pas remplacer le récit projet")

    generic_forbidden = (
        "l operation vise a traiter une incertitude ou un verrou technique",
        "les passages voisins ou semantiquement lies permettent de reconstruire",
        "les resultats rapportent les valeurs source suivantes",
        "les maillons manquants ou ambigus doivent etre documentes",
    )
    if any(fragment in normalized for fragment in generic_forbidden):
        errors.append("la conclusion reprend un gabarit générique au lieu de raconter l'opération réelle")

    # Interdit les listes de nombres sans sémantique (ex. « 99 %, 4, 84 %, 90 % »).
    if re.search(r"(?:\d+(?:[.,]\d+)?\s*%?\s*[,;]\s*){3,}\d+(?:[.,]\d+)?\s*%?", text):
        errors.append("liste brute de nombres détectée : chaque valeur doit être reliée à une métrique et un objet")
    if not re.search(r"\d+(?:[.,]\d+)?\s*%", text):
        errors.append("le niveau de défendabilité doit être nommé en pourcentage")

    required_markers = {
        "verrou ou incertitude": r"\b(?:verrou|incertitude)\w*\b",
        "hypothèse": r"\bhypoth\w*\b",
        "méthode ou expérimentation": r"\b(?:method|outil|protocole|demarche|experiment|essai|comparaison)\w*\b",
        "résultat ou apprentissage": r"\b(?:resultat|mesure|observation|apprentissage|connaissance)\w*\b",
        "critères Frascati": r"\b(?:frascati|nouveaute|creativite|transferabilite|reproductibilite)\w*\b",
        "part restant à consolider": r"\b(?:restant|reste|consolider|partiel|manquant|lacune)\w*\b",
        "conclusion d’éligibilité": r"\b(?:eligib|defendabil|cir)\w*\b",
    }
    for label, pattern in required_markers.items():
        if not re.search(pattern, normalized):
            errors.append(f"élément narratif absent : {label}")

    claims = [
        claim
        for paragraph in paragraphs
        for claim in (paragraph.get("claims") or [])
        if isinstance(claim, dict) and clean_text(claim.get("text"))
    ]
    if len(claims) < 5:
        errors.append("le paragraphe doit être découpé en affirmations sourçables couvrant toute la chaîne technique")

    calculation = next(
        (
            item for item in evidence
            if isinstance(item, dict) and str(item.get("evidence_id") or "") == "F0"
        ),
        {},
    )
    for criterion in calculation.get("criteria_assessment") or []:
        if not isinstance(criterion, dict):
            continue
        label = clean_text(criterion.get("label"), 160)
        normalized_label = _grounding_norm(label)
        criterion_patterns = {
            "nouveaute": (r"\bnouveaute\b", r"\betat de l art\b", r"\bexistant\b"),
            "creativite": (r"\bcreativite\b", r"\boriginal\w*\b", r"\bconception\w*\b"),
            "incertitude scientifique ou technique": (r"\bincertitude\w*\b", r"\bverrou\w*\b"),
            "demarche systematique": (r"\bdemarche systematique\b", r"\bdemarche structuree\b", r"\bprotocole\w*\b"),
            "transferabilite ou reproductibilite": (r"\btransferabilite\b", r"\breproductibilite\b", r"\breutilis\w*\b"),
        }
        patterns = criterion_patterns.get(normalized_label)
        if patterns:
            matching_claims = [
                claim for claim in claims
                if any(re.search(pattern, _grounding_norm(claim.get("text"))) for pattern in patterns)
            ]
        else:
            label_variants = [
                part.strip()
                for part in re.split(r"\s+ou\s+|/", normalized_label)
                if len(part.strip()) >= 5
            ] or [normalized_label]
            matching_claims = [
                claim for claim in claims
                if any(variant and variant in _grounding_norm(claim.get("text")) for variant in label_variants)
            ]
        if not matching_claims:
            errors.append(f"critère Frascati non expliqué dans le récit : {label}")
            continue
        if clean_text(criterion.get("status")) == "documented":
            continue
        try:
            gap_value = float(criterion.get("remaining_gap_to_full_coverage"))
            gap_percent = gap_value * 100 if gap_value <= 1 else gap_value
            expected_number = f"{gap_percent:.10f}".rstrip("0").rstrip(".")
        except Exception:
            expected_number = ""
        if expected_number and not any(
            expected_number in _number_tokens(claim.get("text")) for claim in matching_claims
        ):
            errors.append(f"part manquante non explicitée pour le critère : {label}")
    return errors


def _normalize_llm_section_payload(
    raw: Any,
    parsed: Dict[str, Any],
    config: SectionContextConfig,
) -> Dict[str, Any]:
    """Normalise plusieurs formes de réponse LLM vers le contrat historique.

    Pour la justification Frascati, accepte :
    - {"paragraphs": [...]} ;
    - {"paragraph": {...}} ;
    - {"text": "...", "claims": [...]} ;
    - {"body": "...", "claims": [...]} ;
    - un texte brut en dernier recours.
    """
    data = dict(parsed or {})
    if config.key != "justification_frascati":
        return data

    if isinstance(data.get("paragraphs"), list):
        return data

    if isinstance(data.get("paragraph"), dict):
        data["paragraphs"] = [data["paragraph"]]
        return data

    claims = data.get("claims") if isinstance(data.get("claims"), list) else []
    text = clean_text(
        data.get("text") or data.get("body") or data.get("answer") or data.get("content") or "",
        12000,
    )
    evidence_ids = data.get("evidence_ids") if isinstance(data.get("evidence_ids"), list) else []

    if not text and claims:
        text = clean_text(
            " ".join(
                clean_text(item.get("text"), 1200)
                for item in claims
                if isinstance(item, dict) and clean_text(item.get("text"))
            ),
            12000,
        )

    if text or claims:
        data["paragraphs"] = [{
            "text": text,
            "evidence_ids": evidence_ids,
            "claims": claims,
        }]
        return data

    # Dernier recours : si le modèle a renvoyé du texte non JSON, on le garde
    # au lieu de produire un faux "tout est absent". Les citations seront
    # contrôlées ensuite et le résultat restera marqué avec avertissements.
    if isinstance(raw, str):
        raw_text = raw.strip()
        if raw_text and not raw_text.startswith("{"):
            data["paragraphs"] = [{"text": raw_text, "evidence_ids": [], "claims": []}]
    return data


def parse_section_result(
    raw: Any,
    config: SectionContextConfig,
    evidence: List[Dict[str, Any]],
    project_scope_text: str = "",
) -> Dict[str, Any]:
    parsed = _normalize_llm_section_payload(
        raw,
        extract_json_object(raw),
        config,
    )
    allowed = [item["evidence_id"] for item in evidence]
    paragraphs: List[Dict[str, Any]] = []
    items: List[Dict[str, Any]] = []

    if config.display_mode == "numbered_items":
        raw_items = parsed.get("items") if isinstance(parsed.get("items"), list) else []
        for index, raw_item in enumerate(raw_items[:config.max_items], start=1):
            if not isinstance(raw_item, dict):
                continue
            text = sanitize_generated_text(raw_item.get("text"), max_chars=900)
            if not text:
                continue
            evidence_ids = _valid_evidence_ids(raw_item.get("evidence_ids"), allowed)
            if not evidence_ids:
                evidence_ids = _infer_evidence_ids_for_text(text, evidence, max_ids=2)
            items.append({
                "label": _label_for(config, index, raw_item.get("label")),
                "text": text,
                "evidence_ids": evidence_ids,
            })
    else:
        raw_paragraphs = parsed.get("paragraphs") if isinstance(parsed.get("paragraphs"), list) else []
        for raw_paragraph in raw_paragraphs[:config.max_items]:
            if not isinstance(raw_paragraph, dict):
                continue
            raw_claims = raw_paragraph.get("claims") if isinstance(raw_paragraph.get("claims"), list) else []
            claims: List[Dict[str, Any]] = []
            for raw_claim in raw_claims[:12]:
                if not isinstance(raw_claim, dict):
                    continue
                claim_text = sanitize_generated_text(raw_claim.get("text"), max_chars=700)
                if not claim_text:
                    continue
                claims.append({
                    "claim_kind": clean_text(raw_claim.get("claim_kind"), 80),
                    "text": claim_text,
                    "evidence_ids": _valid_evidence_ids(raw_claim.get("evidence_ids"), allowed),
                })
            text = sanitize_generated_text(raw_paragraph.get("text"), max_chars=5000)
            if config.key == "justification_frascati" and claims:
                text = clean_text(" ".join(claim.get("text") or "" for claim in claims), 5000)
            if not text and claims:
                text = clean_text(" ".join(claim.get("text") or "" for claim in claims), 5000)
            if not text:
                continue
            paragraph_evidence_ids = _valid_evidence_ids(raw_paragraph.get("evidence_ids"), allowed)
            if claims:
                paragraph_evidence_ids = list(dict.fromkeys([
                    *paragraph_evidence_ids,
                    *(evidence_id for claim in claims for evidence_id in claim.get("evidence_ids") or []),
                ]))
            paragraphs.append({
                "text": text,
                "evidence_ids": paragraph_evidence_ids,
                "claims": claims,
            })

    if config.display_mode == "numbered_items":
        body = "\n\n".join(f"{item['label']} — {item['text']}" for item in items)
        used_ids = [eid for item in items for eid in item["evidence_ids"]]
    else:
        body = "\n\n".join(item["text"] for item in paragraphs)
        used_ids = [eid for item in paragraphs for eid in item["evidence_ids"]]

    used_ids = list(dict.fromkeys(used_ids))
    used_evidence = [item for item in evidence if item["evidence_id"] in used_ids]
    grounding_errors: List[str] = []
    grounding_errors.extend(_current_project_scope_errors(body, evidence, config.key, project_scope_text))
    unsupported_terms = _unsupported_domain_terms(body, evidence)
    if unsupported_terms:
        grounding_errors.append(
            "termes de domaine absents des preuves : " + ", ".join(unsupported_terms)
        )
    paragraph_claims = [
        claim
        for paragraph in paragraphs
        for claim in (paragraph.get("claims") or [])
        if isinstance(claim, dict)
    ]
    grounding_units = paragraph_claims if config.key == "justification_frascati" and paragraph_claims else paragraphs
    grounding_errors.extend(_evidence_grounding_errors(grounding_units, items, evidence))
    grounding_errors.extend(_comparison_consistency_errors(body))
    grounding_errors.extend(_unsupported_target_errors(body, evidence, config.key))
    grounding_errors.extend(_truncated_term_errors(body, evidence))
    grounding_errors.extend(
        _strict_claim_grounding_errors(grounding_units, items, evidence, config.key)
    )
    if config.key == "justification_frascati":
        grounding_errors.extend(_eligibility_narrative_errors(body, paragraphs, evidence))
        grounding_errors.extend(_repetition_errors(paragraph_claims or paragraphs, items))
    _attach_proof_quotes(paragraphs, evidence)
    _attach_proof_quotes(paragraph_claims, evidence)
    _attach_proof_quotes(items, evidence)
    minimum_reached = (
        len(items) >= config.min_items
        if config.display_mode == "numbered_items"
        else len(paragraphs) >= config.min_items
    )
    return {
        "body": clean_text(body, 5200),
        "paragraphs": paragraphs,
        "items": items,
        "evidence_ids": used_ids,
        "evidence": used_evidence,
        "validation_errors": grounding_errors,
        "unsupported_domain_terms": unsupported_terms,
        "valid": bool(body) and minimum_reached and not grounding_errors,
    }


def _fallback_from_evidence(config: SectionContextConfig, evidence: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not evidence:
        body = "Information insuffisante dans les preuves du projet courant ; cette section doit être complétée par le consultant."
        return {"body": body, "paragraphs": [{"text": body, "evidence_ids": []}], "items": [], "evidence_ids": [], "evidence": [], "valid": True}

    selected = evidence[:config.max_items]
    if config.display_mode == "numbered_items":
        items = []
        for index, ev in enumerate(selected, start=1):
            text = sanitize_generated_text(ev.get("excerpt"), 780)
            if text:
                items.append({
                    "label": _label_for(config, index, ""),
                    "text": text,
                    "evidence_ids": [ev["evidence_id"]],
                })
        body = "\n\n".join(f"{item['label']} — {item['text']}" for item in items)
        used = [eid for item in items for eid in item["evidence_ids"]]
        _attach_proof_quotes(items, evidence)
        return {"body": body, "paragraphs": [], "items": items, "evidence_ids": used, "evidence": selected[:len(items)], "valid": bool(body)}

    paragraphs = []
    for ev in selected:
        text = sanitize_generated_text(ev.get("excerpt"), 900)
        if text:
            paragraphs.append({"text": text, "evidence_ids": [ev["evidence_id"]]})
    body = "\n\n".join(item["text"] for item in paragraphs)
    used = [eid for item in paragraphs for eid in item["evidence_ids"]]
    _attach_proof_quotes(paragraphs, evidence)
    return {"body": body, "paragraphs": paragraphs, "items": [], "evidence_ids": used, "evidence": selected[:len(paragraphs)], "valid": bool(body)}


def _eligibility_fallback_from_report(
    frascati_summary: Dict[str, Any],
    evidence: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Fallback sûr : jamais de concaténation des opérations ni de nombres bruts.

    Le fallback n'essaie pas d'inventer le récit technique en l'absence d'une
    génération LLM valide. Il conserve le verdict déterministe, les critères et
    les preuves cliquables de l'opération de référence.
    """
    report = frascati_summary.get("eligibility_evidence_report")
    report = report if isinstance(report, dict) else {}
    basis = report.get("score_basis_operation") if isinstance(report.get("score_basis_operation"), dict) else {}
    reference = report.get("reference_operation") if isinstance(report.get("reference_operation"), dict) else basis
    criteria = [item for item in ((reference or basis).get("criteria") or []) if isinstance(item, dict)]

    score = report.get("score")
    remaining = report.get("remaining_documentary_gap")
    reference_status = clean_text(reference.get("operation_status"))

    project_evidence = [
        item for item in evidence
        if isinstance(item, dict) and str(item.get("evidence_id")) != "F0"
    ][:8]
    calculation = next(
        (item for item in evidence if isinstance(item, dict) and str(item.get("evidence_id")) == "F0"),
        None,
    )
    selected = ([calculation] if calculation else []) + project_evidence
    selected = [item for item in selected if isinstance(item, dict)]
    evidence_ids = [str(item.get("evidence_id")) for item in selected if item.get("evidence_id")]

    documented = [
        clean_text(item.get("label") or item.get("criterion"), 160)
        for item in criteria if clean_text(item.get("status")) == "documented"
    ]
    partial = [
        (
            clean_text(item.get("label") or item.get("criterion"), 160),
            _percent_text(item.get("remaining_gap_to_full_coverage")),
            clean_text(item.get("reason_fr") or item.get("reason"), 420),
        )
        for item in criteria if clean_text(item.get("status")) == "partial"
    ]

    status_text = {
        "rnd_core_defendable": "un noyau R&D défendable est identifié",
        "rnd_core_partial": "un noyau R&D partiel est identifié mais doit être consolidé",
        "classical_engineering": "l'opération de référence relève de l'ingénierie classique selon le garde métier",
        "insufficient_evidence": "les preuves restent insuffisantes pour qualifier l'opération de référence",
    }.get(reference_status, "la qualification de l'opération de référence doit être validée")

    claim1 = (
        "La génération projet-spécifique n'a pas satisfait le contrôle factuel ; le système conserve donc uniquement "
        f"la lecture déterministe : {status_text}. Les preuves cliquables de l'opération de référence sont disponibles "
        "pour permettre au consultant de reconstruire le verrou, l'hypothèse, l'expérimentation et les résultats sans hallucination."
    )
    claim2 = (
        f"L'indice de défendabilité R&D est de {_percent_text(score)}. "
        + (
            "Les critères intégralement documentés sont : " + ", ".join(documented) + "."
            if documented else "Aucun critère n'est intégralement documenté dans l'opération de référence."
        )
    )
    if partial:
        details = "; ".join(
            f"{label} : {gap} restent à consolider" + (f" ({reason})" if reason else "")
            for label, gap, reason in partial
        )
        claim3 = f"La part restant à consolider est de {_percent_text(remaining)}. {details}."
    else:
        claim3 = f"La part restant à consolider est de {_percent_text(remaining)}."

    if reference_status == "classical_engineering":
        claim4 = "EnnoDiagnostic ne retient pas cette opération comme noyau R&D éligible potentiel ; validation consultant obligatoire."
    elif reference_status == "insufficient_evidence":
        claim4 = "EnnoDiagnostic ne conclut pas sans preuves complémentaires ; validation consultant obligatoire."
    else:
        claim4 = "EnnoDiagnostic retient une éligibilité potentielle à confirmer par le consultant CIR."

    claims = [
        {"claim_kind": "audit_fallback", "text": clean_text(claim1, 1500), "evidence_ids": [eid for eid in evidence_ids if eid != "F0"]},
        {"claim_kind": "criteres_acquis", "text": clean_text(claim2, 1000), "evidence_ids": evidence_ids},
        {"claim_kind": "criteres_a_consolider", "text": clean_text(claim3, 1400), "evidence_ids": evidence_ids},
        {"claim_kind": "conclusion", "text": clean_text(claim4, 600), "evidence_ids": evidence_ids},
    ]
    body = clean_text(" ".join(item["text"] for item in claims), 5200)
    paragraphs = [{"text": body, "evidence_ids": evidence_ids, "claims": claims}]
    _attach_proof_quotes(paragraphs, evidence)
    _attach_proof_quotes(claims, evidence)
    return {
        "body": body,
        "paragraphs": paragraphs,
        "items": [],
        "evidence_ids": evidence_ids,
        "evidence": selected,
        "valid": bool(body),
    }


def _section_fallback(
    config: SectionContextConfig,
    frascati_summary: Dict[str, Any],
    evidence: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if config.key == "justification_frascati":
        return _eligibility_fallback_from_report(frascati_summary, evidence)
    return _fallback_from_evidence(config, evidence)


def generate_one_section(
    llm: Any,
    config: SectionContextConfig,
    sections: Dict[str, List[Dict[str, Any]]],
    frascati_summary: Dict[str, Any],
    style_memory_report: Optional[Dict[str, Any]] = None,
    memory_v2_report: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], str]:
    prompt, evidence = build_section_context(
        config,
        sections,
        frascati_summary,
        style_memory_report=style_memory_report,
        memory_v2_report=memory_v2_report,
    )
    project_scope_text = _project_scope_text_from_sections(sections)
    telemetry: Dict[str, Any] = {
        "configured_max_input_tokens": config.max_input_tokens,
        "configured_max_output_tokens": config.max_output_tokens,
        "estimated_prompt_tokens": estimate_tokens(prompt),
        "prompt_chars": len(prompt),
        "evidence_count": len(evidence),
        "display_mode": config.display_mode,
    }

    # La conclusion Frascati ne passe plus par du JSON texte libre.
    # PydanticAI force le schéma, valide et relance automatiquement le modèle
    # si la structure ou le contrat factuel ne sont pas respectés.
    if config.key == "justification_frascati" and generate_eligibility_section_with_pydantic_ai is not None:
        try:
            structured = generate_eligibility_section_with_pydantic_ai(
                frascati_summary=frascati_summary,
                evidence=evidence,
            )
            framework_telemetry = structured.get("telemetry") if isinstance(structured.get("telemetry"), dict) else {}
            structured["telemetry"] = {**telemetry, **framework_telemetry}
            print(
                "[EnnoDiagnostic][PYDANTIC_AI] "
                f"section={config.key} status={structured.get('status')} "
                f"claims={len(((structured.get('paragraphs') or [{}])[0]).get('claims') or [])}"
            )
            return structured, clean_text(structured.get("framework_prompt") or prompt, 30000)
        except Exception as exc:
            print(
                "[EnnoDiagnostic][PYDANTIC_AI_ERROR] "
                f"section={config.key} error={type(exc).__name__}: {exc}"
            )
            # Ne jamais retomber sur l'ancien gros JSON libre : c'est ce chemin
            # qui tronquait la sortie puis produisait parsed_keys=[]. Si le
            # framework échoue encore, on conserve immédiatement la lecture
            # déterministe et les preuves cliquables, sans deux appels LLM inutiles.
            fallback = _eligibility_fallback_from_report(frascati_summary, evidence)
            fallback.update({
                "status": "pydantic_ai_failed_deterministic_fallback",
                "error": f"{type(exc).__name__}: {exc}",
                "telemetry": telemetry,
            })
            return fallback, prompt

    if llm is None:
        result = _section_fallback(config, frascati_summary, evidence)
        result.update({"status": "fallback_without_llm", "telemetry": telemetry})
        return result, prompt

    try:
        raw = llm.generate(
            prompt,
            temperature=config.temperature,
            max_input_tokens=config.max_input_tokens,
            max_output_tokens=config.max_output_tokens,
            retries=1,
            json_mode=True,
            request_name=f"ennodiagnostic:{config.key}",
        )
        parsed = parse_section_result(raw, config, evidence, project_scope_text=project_scope_text)
        llm_meta = {}
        try:
            llm_meta = llm.get_last_generation_meta()
        except Exception:
            llm_meta = getattr(llm, "last_generation_meta", {}) or {}
        telemetry.update(llm_meta if isinstance(llm_meta, dict) else {})

        if parsed.get("valid"):
            parsed.update({"status": "llm_section_json", "telemetry": telemetry})
            return parsed, prompt

        validation_errors = parsed.get("validation_errors") or []
        if validation_errors:
            print(
                f"[EnnoDiagnostic][SECTION_GUARD] section={config.key} first_reject="
                + " | ".join(str(error) for error in validation_errors[:12])
            )
            retry_prompt = (
                prompt
                + "\n\nCORRECTION FACTUELLE OBLIGATOIRE :\n"
                + "La réponse précédente n'a pas respecté le contrat factuel : "
                + "; ".join(str(error) for error in validation_errors)
                + ". Réécris entièrement le JSON. Chaque paragraphe ou élément doit avoir "
                  "au moins un evidence_id. Ne change ni le domaine ni la nature des objets, "
                  "ne transforme aucun résultat en objectif, et signale explicitement toute "
                  "comparaison numérique contradictoire. Supprime toute valeur absente des preuves citées, "
                  "toute causalité non formulée par une preuve et toute répétition. Ne calcule aucun gain "
                  "ou écart. Utilise uniquement les PREUVES ci-dessus."
            )
            raw_retry = llm.generate(
                retry_prompt,
                temperature=0.0,
                max_input_tokens=config.max_input_tokens,
                max_output_tokens=config.max_output_tokens,
                retries=0,
                json_mode=True,
                request_name=f"ennodiagnostic:{config.key}:grounding_retry",
            )
            retry_parsed = parse_section_result(raw_retry, config, evidence, project_scope_text=project_scope_text)
            telemetry["grounding_retry"] = True
            telemetry["first_validation_errors"] = validation_errors
            retry_validation_errors = retry_parsed.get("validation_errors") or []
            if retry_validation_errors:
                print(
                    f"[EnnoDiagnostic][SECTION_GUARD] section={config.key} retry_reject="
                    + " | ".join(str(error) for error in retry_validation_errors[:12])
                )
                telemetry["retry_validation_errors"] = retry_validation_errors
            if retry_parsed.get("valid"):
                retry_parsed.update({
                    "status": "llm_section_json_after_grounding_retry",
                    "telemetry": telemetry,
                })
                return retry_parsed, retry_prompt

            # Pour l'objectif global, un rejet d'ancrage ne doit jamais être
            # accepté en warning_only : on préfère un extrait directement sourcé
            # du projet courant à un objectif potentiellement contaminé.
            if config.key == "objectif_global" and retry_validation_errors:
                grounded = _fallback_from_evidence(config, evidence)
                grounded.update({
                    "status": "grounded_evidence_fallback_after_objective_reject",
                    "validation_errors": retry_validation_errors,
                    "telemetry": telemetry,
                })
                return grounded, retry_prompt

            # Les contrôles sont des avertissements après le retry pour les autres sections.
            if clean_text(retry_parsed.get("body"), 5200):
                retry_parsed.update({
                    "valid": True,
                    "status": "llm_section_json_with_validation_warnings",
                    "validation_errors": retry_validation_errors,
                    "validation_warnings_only": True,
                    "telemetry": telemetry,
                })
                print(
                    f"[EnnoDiagnostic][SECTION_GUARD] section={config.key} "
                    "mode=warning_only action=accept_retry_text"
                )
                return retry_parsed, retry_prompt

        # Même principe si le retry n'a rien fourni mais que la première génération
        # contient déjà du texte exploitable.
        if clean_text(parsed.get("body"), 5200):
            parsed.update({
                "valid": True,
                "status": "llm_section_json_with_validation_warnings",
                "validation_errors": parsed.get("validation_errors") or [],
                "validation_warnings_only": True,
                "telemetry": telemetry,
            })
            print(
                f"[EnnoDiagnostic][SECTION_GUARD] section={config.key} "
                "mode=warning_only action=accept_first_text"
            )
            return parsed, prompt

        if not clean_text(parsed.get("body"), 5200):
            raw_preview = clean_text(raw, 900).replace("\n", " ")
            parsed_keys = list(extract_json_object(raw).keys())[:20]
            print(
                f"[EnnoDiagnostic][SECTION_PARSE] section={config.key} "
                f"raw_type={type(raw).__name__} parsed_keys={parsed_keys} "
                f"raw_preview={raw_preview[:700]}"
            )
        fallback = _section_fallback(config, frascati_summary, evidence)
        fallback.update({
            "status": "fallback_after_invalid_section_json",
            "validation_errors": parsed.get("validation_errors") or [],
            "telemetry": telemetry,
        })
        return fallback, prompt
    except Exception as exc:
        fallback = _section_fallback(config, frascati_summary, evidence)
        fallback.update({"status": "fallback_after_llm_error", "error": str(exc), "telemetry": telemetry})
        return fallback, prompt


def generate_structured_diagnostic_core(
    llm: Any,
    sections: Dict[str, List[Dict[str, Any]]],
    frascati_summary: Dict[str, Any],
    style_memory_report: Optional[Dict[str, Any]] = None,
    ai_detection_report: Optional[Dict[str, Any]] = None,
    memory_v2_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    del ai_detection_report
    configs = all_section_configs()
    section_payloads: Dict[str, Dict[str, Any]] = {}
    prompts: Dict[str, str] = {}
    values: Dict[str, str] = {}
    errors: Dict[str, str] = {}

    for key in CORE_LLM_KEYS:
        payload, prompt = generate_one_section(
            llm,
            configs[key],
            sections,
            frascati_summary,
            style_memory_report=style_memory_report,
            memory_v2_report=memory_v2_report,
        )
        section_payloads[key] = payload
        prompts[key] = prompt
        values[key] = clean_text(payload.get("body"), 5000)
        if payload.get("error"):
            errors[key] = str(payload["error"])

    token_usage = {
        key: dict(payload.get("telemetry") or {})
        for key, payload in section_payloads.items()
    }
    statuses = {key: payload.get("status") for key, payload in section_payloads.items()}
    llm_statuses = {"llm_section_json", "llm_section_json_after_grounding_retry", "llm_section_json_with_validation_warnings"}
    all_llm = all(status in llm_statuses for status in statuses.values())
    any_llm = any(status in llm_statuses for status in statuses.values())
    status = "llm_sectional_context_engineering" if all_llm else (
        "llm_sectional_with_fallbacks" if any_llm else "fallback_sectional"
    )

    return {
        "ok": not bool(errors),
        "status": status,
        "error": errors or None,
        "prompt": "\n\n--- SECTION ---\n\n".join(prompts.values()),
        "prompt_chars": sum(len(prompt) for prompt in prompts.values()),
        "prompts_by_section": prompts,
        "sections_by_key": values,
        "section_payloads_by_key": section_payloads,
        "token_usage_by_section": token_usage,
        "section_statuses": statuses,
        "context_engineering_version": "v191_current_project_nlp_pack_pydantic_relaxed",
    }


def _percent_text(value: Any) -> str:
    try:
        number = float(value)
    except Exception:
        return "—"
    if number <= 1:
        number *= 100
    rounded = round(number, 1)
    return f"{int(rounded)} %" if rounded.is_integer() else f"{str(rounded).replace('.', ',')} %"


def frascati_summary_text(frascati_summary: Dict[str, Any]) -> str:
    summary = frascati_summary if isinstance(frascati_summary, dict) else {}
    score = summary.get("rnd_defensibility_index")
    if score is None:
        score = summary.get("eligibility_assessment_score")
    documentary_coverage = summary.get("documentary_coverage")
    if documentary_coverage is None:
        documentary_coverage = summary.get("average_frascati_score")
    report = summary.get("eligibility_evidence_report")
    report = report if isinstance(report, dict) else {}
    basis = report.get("score_basis_operation") if isinstance(report.get("score_basis_operation"), dict) else {}
    reference = report.get("reference_operation") if isinstance(report.get("reference_operation"), dict) else basis
    remaining = report.get("remaining_documentary_gap")
    if remaining is None and score is not None:
        try:
            remaining = max(0.0, 1.0 - float(score))
        except Exception:
            remaining = None
    parts: List[str] = []
    if score is not None:
        parts.append(
            f"L'indice de défendabilité R&D est de {_percent_text(score)}. "
            f"La part documentaire restant à consolider est de {_percent_text(remaining)}."
        )
    if basis:
        parts.append(
            "Cet indice conserve la couverture Frascati calculée en amont pour l’opération désignée par le calcul officiel."
        )
    elif documentary_coverage is not None:
        parts.append(
            f"La couverture documentaire de l'opération de référence est de {_percent_text(documentary_coverage)}."
        )
    if reference:
        status_labels = {
            "rnd_core_defendable": "noyau R&D défendable",
            "rnd_core_partial": "noyau R&D partiel à consolider",
            "insufficient_evidence": "preuves insuffisantes, validation requise",
            "classical_engineering": "ingénierie classique",
        }
        reference_index = next(
            (
                index for index, operation in enumerate(report.get("operations") or [], start=1)
                if isinstance(operation, dict) and operation.get("group_id") == reference.get("group_id")
            ),
            1,
        )
        parts.append(
            f"L’opération {reference_index} est retenue comme référence pour l’étude des preuves. Sa lecture documentaire est : "
            + status_labels.get(clean_text(reference.get("operation_status")), "à qualifier")
            + "."
        )

    criteria = [item for item in (basis.get("criteria") or []) if isinstance(item, dict)]
    if criteria:
        criterion_lines = []
        for item in criteria:
            contribution = _percent_text(item.get("contribution_to_index"))
            gap = _percent_text(item.get("remaining_gap_to_full_coverage"))
            criterion_lines.append(
                f"{item.get('label') or item.get('criterion')} : statut {item.get('status')}, "
                f"{contribution} acquis et {gap} à compléter. {clean_text(item.get('reason'), 360)}"
            )
        parts.append(
            "Décomposition des cinq critères de poids égal : " + " ".join(criterion_lines)
        )
    parts.append(
        "Chaque critère pèse 20 % : documenté = 20 %, partiel = 10 %, manquant ou contradictoire = 0 %. "
        "La nature R&D ou classique de la démarche est contrôlée séparément. Les extraits affichés sous chaque "
        "critère sont les preuves qui fondent cette lecture ; l'indice n'est pas une probabilité d'acceptation administrative."
    )
    return clean_text("\n\n".join(parts), 4200)


def demarche_legibility_text(frascati_summary: Dict[str, Any]) -> str:
    """Rend le verdict deterministe lisible dans le frontend du diagnostic."""
    summary = frascati_summary if isinstance(frascati_summary, dict) else {}
    audit = summary.get("demarche_legibility") if isinstance(summary.get("demarche_legibility"), dict) else {}
    if not audit:
        return ""

    labels = {
        "rnd_core_defendable": "au moins un noyau R&D défendable",
        "rnd_core_partial": "noyau R&D partiel à compléter",
        "classical_engineering": "ingénierie classique sans noyau R&D défendable",
        "insufficient_evidence": "preuves insuffisantes pour qualifier le noyau R&D",
    }
    status = str(audit.get("project_status") or audit.get("operation_status") or "insufficient_evidence")
    label = labels.get(status, status.replace("_", " "))
    total_operations = int(audit.get("operations_count") or audit.get("operation_count") or 0)
    defendable = int(audit.get("rnd_core_defendable_operations_count") or (1 if status == "rnd_core_defendable" else 0))
    partial = int(audit.get("rnd_core_partial_operations_count") or (1 if status == "rnd_core_partial" else 0))
    classical_operations = int(audit.get("classical_engineering_operations_count") or (1 if status == "classical_engineering" else 0))
    insufficient_operations = int(audit.get("insufficient_evidence_operations_count") or (1 if status == "insufficient_evidence" and total_operations else 0))
    activities = int(audit.get("activities_count") or 0)
    direct_rnd = int(audit.get("direct_rnd_activities_count") or 0)
    support = int(audit.get("necessary_rnd_support_activities_count") or 0)
    classical_activities = int(audit.get("classical_engineering_activities_count") or 0)
    insufficient_activities = int(audit.get("insufficient_evidence_activities_count") or 0)

    parts = [
        "Contrôle de pertinence des démarches",
        (
            f"Conclusion : {label}. {total_operations} opération(s) consolidée(s) ont été analysées : "
            f"{defendable} noyau(x) R&D défendable(s), {partial} partiel(s), "
            f"{classical_operations} classique(s) et {insufficient_operations} insuffisamment documentée(s)."
        ),
        (
            f"Les {activities} activités internes ne sont pas traitées comme autant d'opérations R&D : "
            f"{direct_rnd} relèvent directement de la R&D, {support} sont des supports techniques nécessaires, "
            f"{classical_activities} relèvent de l'ingénierie classique et {insufficient_activities} restent à rattacher."
        ),
        (
            "Une validation, une mesure de performance ou un test classique n'est jamais classé R&D directe à lui seul : "
            "il devient support R&D seulement si son rattachement au protocole qui traite l'incertitude est prouvé ; "
            "sinon il relève de l'ingénierie classique ou reste à documenter."
        ),
    ]
    if status == "classical_engineering" and defendable == 0:
        parts.append(
            "Décision de garde : cette opération est non éligible potentielle. Si toutes les opérations du projet "
            "sont dans ce cas, l'indice de défendabilité est ramené à 0."
        )
    else:
        parts.append(
            "Les éléments insuffisamment expliqués augmentent le risque et réduisent la confiance documentaire ; "
            "ils ne diminuent pas mécaniquement la couverture Frascati."
        )
    if audit.get("direct_final_solution_risk"):
        parts.append(
            "Raccourci possible : le dossier n'exclut pas encore que la solution finale aurait pu être "
            "choisie dès le départ à partir des connaissances accessibles."
        )
    if audit.get("llm_review_recommended"):
        parts.append(
            "Le cas est ambigu : l'appel LLM déjà utilisé pour rédiger cette section approfondit les preuves, "
            "sans appel supplémentaire et sans remplacer la validation du consultant."
        )
    questions = [str(value) for value in (audit.get("questions_to_ask") or []) if value]
    if questions:
        parts.append("Points à justifier : " + " ".join(f"{index}. {value}" for index, value in enumerate(questions[:3], 1)))
    return clean_text("\n\n".join(parts), 3000)


def memory_v2_usage_text(report: Optional[Dict[str, Any]]) -> str:
    data = report if isinstance(report, dict) else {}
    if not data:
        return "Aucun contexte de mémoire V2 exploitable n'a été utilisé pour ce diagnostic."
    experience = data.get("experience") if isinstance(data.get("experience"), dict) else {}
    style = data.get("style") if isinstance(data.get("style"), dict) else {}
    parts: List[str] = []
    if experience.get("used"):
        parts.append(f"La mémoire d'expérience a orienté l'analyse à partir de {experience.get('cards_used', 0)} carte(s) pertinente(s).")
    if style.get("used"):
        parts.append(f"La mémoire de style a fourni {style.get('examples_used', 0)} exemple(s) rédactionnel(s).")
    parts.append("La mémoire n'est jamais utilisée comme preuve factuelle du dossier courant.")
    return clean_text(" ".join(parts), 1600)


def verrous_text_from_items(items: List[Dict[str, Any]], max_items: Optional[int] = None) -> str:
    if not isinstance(items, list) or not items:
        return "Aucun signal de verrou R&D exploitable n'a été consolidé automatiquement."
    output: List[str] = []
    visible_items = items if max_items is None else items[:max_items]
    for index, item in enumerate(visible_items, start=1):
        if not isinstance(item, dict):
            continue
        title = sanitize_generated_text(item.get("title"), 260)
        explanation = sanitize_generated_text(
            item.get("consultant_explanation")
            or item.get("why_agent_found_verrou")
            or item.get("justification")
            or item.get("text"),
            800,
        )
        if title or explanation:
            context_only = (
                str(item.get("candidate_status") or "").lower() == "context_only"
                or bool(item.get("not_final_cir"))
            )
            label = "Contexte à qualifier" if context_only else "Signal R&D candidat"
            output.append(clean_text(f"{label} {index} — {title}. {explanation}", 1100))
    return "\n\n".join(output) or "Aucun signal de verrou R&D exploitable n'a été consolidé automatiquement."


def _legacy_frascati_text(result: Dict[str, Any]) -> str:
    candidates = []
    if isinstance(result, dict):
        candidates.extend([result.get("text"), result.get("content")])
        generation = result.get("generation")
        if isinstance(generation, dict):
            candidates.append(generation.get("content"))
    for candidate in candidates:
        text = sanitize_generated_text(candidate, 2800)
        if text:
            return text
    return ""


def paragraphs_from_body(body: str, max_items: int = 6) -> List[str]:
    return [clean_text(item, 1100) for item in re.split(r"\n\s*\n+", clean_text(body, 6000)) if item.strip()][:max_items]


def build_cards(
    sections_by_key: Dict[str, str],
    payloads_by_key: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    for definition in STATIC_SECTION_DEFINITIONS:
        key = definition["key"]
        body = clean_text(sections_by_key.get(key), 6000)
        payload = payloads_by_key.get(key) if isinstance(payloads_by_key.get(key), dict) else {}
        cards.append({
            "key": key,
            "title": definition["title"],
            "description": definition["description"],
            "body": body,
            "paragraphs": payload.get("paragraphs") or paragraphs_from_body(body),
            "items": payload.get("items") or [],
            "evidence": payload.get("evidence") or [],
            "evidence_ids": payload.get("evidence_ids") or [],
            "eligibility_evidence_report": payload.get("eligibility_evidence_report") or {},
            "proof_policy": payload.get("proof_policy"),
            "display_mode": (payload.get("telemetry") or {}).get("display_mode") or "paragraphs",
            "format": "structured_plain_text_with_evidence",
            "is_empty": not bool(body),
            "hidden_in_diagnostic_ui": key == "verrous_rnd",
        })
    return cards


def build_plain_report(cards: List[Dict[str, Any]]) -> str:
    blocks = []
    for card in cards:
        if card.get("hidden_in_diagnostic_ui"):
            continue
        blocks.append(f"{card.get('title')}\n\n{card.get('body') or 'Information non disponible.'}".strip())
    return "\n\n".join(blocks).strip()


def build_final_static_diagnostic(
    core_result: Dict[str, Any],
    sections: Dict[str, List[Dict[str, Any]]],
    frascati_summary: Dict[str, Any],
    frascati_justification_result: Optional[Dict[str, Any]] = None,
    memory_v2_usage_report: Optional[Dict[str, Any]] = None,
    llm_reformulated_verrous: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    del sections
    core = core_result if isinstance(core_result, dict) else {}
    raw_values = core.get("sections_by_key") if isinstance(core.get("sections_by_key"), dict) else {}
    payloads = dict(core.get("section_payloads_by_key") or {}) if isinstance(core.get("section_payloads_by_key"), dict) else {}
    sections_by_key = {key: clean_text(value, 6000) for key, value in raw_values.items()}
    # Suppression définitive de l'ancienne section, y compris si un ancien
    # résultat en cache la fournit encore.
    sections_by_key.pop("points_validation", None)
    payloads.pop("points_validation", None)

    frascati_reading = frascati_summary_text(frascati_summary)
    frascati_justification = clean_text(
        sections_by_key.get("justification_frascati")
        or _legacy_frascati_text(frascati_justification_result or {}),
        3600,
    )
    if not frascati_justification:
        frascati_justification = (
            "La justification projet-spécifique n'a pas pu être produite automatiquement. "
            "Le consultant doit la compléter à partir des preuves du dossier."
        )

    # Une seule section visible : score, critères, démarche et analyse LLM
    # strictement sourcée. La clé séparée reste disponible pour l'audit API.
    sections_by_key["lecture_frascati"] = clean_text(
        f"{frascati_reading}\n\n"
        f"Analyse approfondie reliée aux preuves\n\n{frascati_justification}",
        6000,
    )
    sections_by_key["justification_frascati"] = frascati_justification

    justification_payload = payloads.get("justification_frascati")
    if not isinstance(justification_payload, dict):
        justification_payload = {}
    payloads["lecture_frascati"] = {
        "body": sections_by_key["lecture_frascati"],
        "paragraphs": [
            {"text": frascati_reading, "evidence_ids": []},
            *(
                justification_payload.get("paragraphs")
                if isinstance(justification_payload.get("paragraphs"), list)
                else [{
                    "text": frascati_justification,
                    "evidence_ids": justification_payload.get("evidence_ids") or [],
                }]
            ),
        ],
        "items": [],
        "evidence_ids": justification_payload.get("evidence_ids") or [],
        "evidence": justification_payload.get("evidence") or [],
        "eligibility_evidence_report": (
            frascati_summary.get("eligibility_evidence_report")
            if isinstance(frascati_summary.get("eligibility_evidence_report"), dict)
            else {}
        ),
        "proof_policy": "atomic_claims_exact_numbers_explicit_causality_source_quotes_no_repetition",
        "telemetry": {
            **(
                justification_payload.get("telemetry")
                if isinstance(justification_payload.get("telemetry"), dict)
                else {}
            ),
            "display_mode": "paragraphs",
            "merged_into_analysis_frascati": True,
        },
        "status": justification_payload.get("status") or "merged_evidence_grounded_eligibility_analysis",
        "valid": True,
    }

    sections_by_key["memoire_v2"] = memory_v2_usage_text(memory_v2_usage_report)
    sections_by_key["verrous_rnd"] = verrous_text_from_items(llm_reformulated_verrous or [])

    demarche_audit = demarche_legibility_text(frascati_summary)
    if demarche_audit:
        generated_demarche = clean_text(sections_by_key.get("demarche_detectee"), 4200)
        sections_by_key["demarche_detectee"] = clean_text(
            demarche_audit
            + ("\n\nDémarches relevées dans les preuves\n\n" + generated_demarche if generated_demarche else ""),
            6000,
        )

    for definition in STATIC_SECTION_DEFINITIONS:
        key = definition["key"]
        if not sections_by_key.get(key):
            sections_by_key[key] = "Information insuffisante ; cette section doit être complétée par le consultant à partir des documents sources."

    sections_by_title = {
        KEY_TO_TITLE[key]: value
        for key, value in sections_by_key.items()
        if key in KEY_TO_TITLE
    }
    # Alias conservés pour les routes et composants historiques.
    sections_by_title["Analyse Frascati"] = sections_by_key["lecture_frascati"]
    sections_by_title["Lecture Frascati du dossier"] = sections_by_key["lecture_frascati"]
    sections_by_title["Justification Frascati du score"] = sections_by_key["justification_frascati"]
    sections_by_title["Objectif global reformulé"] = sections_by_key["objectif_global"]
    sections_by_title["Démarche expérimentale détectée"] = sections_by_key["demarche_detectee"]
    sections_by_title["Résultats et métriques disponibles"] = sections_by_key["resultats_metriques"]
    sections_by_title["Verrous R&D / signaux de verrous"] = sections_by_key["verrous_rnd"]

    cards = build_cards(sections_by_key, payloads)
    return {
        "ok": True,
            "version": "v153_unified_eligibility_balanced_guard",
        "sections_by_key": sections_by_key,
        "sections_by_title": sections_by_title,
        "section_payloads_by_key": payloads,
        "cards": cards,
        "plain_report": build_plain_report(cards),
        "token_usage_by_section": core.get("token_usage_by_section") or {},
        "section_statuses": core.get("section_statuses") or {},
        "context_engineering": {
            "one_call_per_section": True,
            "sources_separated_from_text": True,
            "evidence_locators_preserved": True,
            "markdown_generated_by_llm": False,
            "version": "v153",
        },
        "format": "structured_plain_text_with_evidence",
    }
