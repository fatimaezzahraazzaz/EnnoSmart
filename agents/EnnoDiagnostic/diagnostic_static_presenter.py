# -*- coding: utf-8 -*-
from __future__ import annotations

"""EnnoDiagnostic V182 — sections structurées avec validation factuelle.

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


STATIC_SECTION_DEFINITIONS: List[Dict[str, str]] = [
    {
        "key": "lecture_frascati",
        "title": "Analyse Frascati",
        "description": (
            "Lecture du score calculé par NLP/Frascati et justification "
            "projet-spécifique générée par le LLM à partir des preuves du dossier."
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
        "description": "Méthodes, protocoles, essais, simulations ou analyses détectés dans les sources.",
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


_INTERNAL_EVIDENCE_TOKEN_RE = re.compile(r"\b(?:E\d+|G\d+\.S\d+)\b", flags=re.I)


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
    text = clean_text(raw, max_chars=30000)
    if not text:
        return {}
    text = re.sub(r"```(?:json)?", "", text, flags=re.I).replace("```", "").strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def meta_of(source: Dict[str, Any]) -> Dict[str, Any]:
    meta = source.get("metadata") if isinstance(source, dict) else {}
    return meta if isinstance(meta, dict) else {}


def source_text(source: Dict[str, Any]) -> str:
    if not isinstance(source, dict):
        return ""
    return clean_text(
        source.get("text")
        or source.get("source_text")
        or source.get("content")
        or source.get("excerpt")
        or "",
        max_chars=4000,
    )


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
        "section_title": clean_text(meta.get("section_title") or "", 240),
        "role": clean_text(meta.get("role") or "", 80),
        "excerpt": clean_text(source_text(source), excerpt_limit),
    }


_SECTION_INSTRUCTIONS = {
    "synthese_strategique": (
        "Rédige deux ou trois paragraphes : contexte et enjeu technique, difficulté R&D, puis portée de l'analyse. "
        "Ne transforme pas un résultat en verrou et reste prudent."
    ),
    "objectif_global": (
        "Rédige un seul paragraphe précis présentant l'objet technique, l'action recherchée et le critère de réussite."
    ),
    "justification_frascati": (
        "Rédige deux ou trois paragraphes. Le premier explique les signaux qui soutiennent le score. "
        "Le deuxième explique les limites et preuves manquantes. Le dernier conclut sur le besoin de validation. "
        "Ne pose aucune question et ne recalcule jamais le score."
    ),
    "demarche_detectee": (
        "Organise les travaux dans l'ordre logique sous forme de démarches numérotées. "
        "Chaque démarche doit expliquer l'objectif de l'étape, la méthode appliquée et ce qu'elle cherche à vérifier."
    ),
    "resultats_metriques": (
        "Rédige des paragraphes thématiques distinguant résultats chiffrés, observations qualitatives et portée limitée. "
        "N'invente aucune valeur et ne recopie pas les extraits."
    ),
    "parametres_contraintes": (
        "Organise les paramètres et contraintes en éléments numérotés. "
        "Pour chacun, explique son influence sur la validité, la robustesse, la représentativité ou le coût."
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
        })
    audit = value.get("rag_audit") if isinstance(value.get("rag_audit"), dict) else {}
    return {
        "average_frascati_score": value.get("average_frascati_score"),
        "scores_count": value.get("scores_count"),
        "main_groups_scores_count": value.get("main_groups_scores_count"),
        "main_groups_average_frascati_score": value.get("main_groups_average_frascati_score"),
        "risk_level": value.get("risk_level"),
        "score_source": value.get("score_source"),
        "decisions_count": value.get("decisions_count"),
        "candidate_levels_count": value.get("candidate_levels_count"),
        "group_assessments": groups,
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
    sources = get_sources(sections, config.source_keys, config.top_k_evidence)
    evidence: List[Dict[str, Any]] = []

    base = {
        "section": config.title,
        "instruction": _SECTION_INSTRUCTIONS[config.key],
        "format": _schema_for(config),
        "frascati": _compact_frascati_for_prompt(frascati_summary) if config.key == "justification_frascati" else {},
    }
    base_prompt = f"""
Tu es EnnoDiagnostic, agent d'analyse CIR. Rédige uniquement la section « {config.title} ».

Règles absolues :
- Utilise seulement les preuves numérotées fournies.
- Les blocs STYLE et MEMORY V2 ne sont jamais des preuves factuelles.
- Utilise STYLE uniquement pour la forme et MEMORY V2 uniquement pour une analogie prudente.
- N'invente aucun fait, résultat, paramètre ou protocole.
- Chaque paragraphe ou élément doit citer au moins un `evidence_id` autorisé.
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

    for index, source in enumerate(sources, start=1):
        item = evidence_from_source(source, f"E{index}", config.max_chars_per_evidence)
        candidate = evidence + [item]
        prompt_candidate = base_prompt.replace(
            "{evidence_json}",
            json.dumps(
                [
                    {
                        "evidence_id": ev["evidence_id"],
                        "role": ev["role"],
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
                {"evidence_id": ev["evidence_id"], "role": ev["role"], "text": ev["excerpt"]}
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
    generated_words = set(re.findall(r"\b[a-z]{3,}\b", _grounding_norm(body)))
    source_words = set(re.findall(r"\b[a-z]{3,}\b", _grounding_norm(" ".join(
        str(item.get("excerpt") or item.get("text") or "")
        for item in evidence if isinstance(item, dict)
    ))))
    common = {
        "avec", "dans", "pour", "sans", "sous", "entre", "plus", "moins",
        "ainsi", "cette", "leurs", "comme", "afin", "dont", "tout", "etre",
    }
    suspicious: List[str] = []
    for word in generated_words - source_words:
        if word in common or len(word) > 5:
            continue
        if any(len(source_word) >= len(word) + 2 and source_word.endswith(word) for source_word in source_words):
            suspicious.append(word)
    return ["termes potentiellement tronqués ou déformés : " + ", ".join(sorted(suspicious))] if suspicious else []


def parse_section_result(
    raw: Any,
    config: SectionContextConfig,
    evidence: List[Dict[str, Any]],
) -> Dict[str, Any]:
    parsed = extract_json_object(raw)
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
            items.append({
                "label": _label_for(config, index, raw_item.get("label")),
                "text": text,
                "evidence_ids": _valid_evidence_ids(raw_item.get("evidence_ids"), allowed),
            })
    else:
        raw_paragraphs = parsed.get("paragraphs") if isinstance(parsed.get("paragraphs"), list) else []
        for raw_paragraph in raw_paragraphs[:config.max_items]:
            if not isinstance(raw_paragraph, dict):
                continue
            text = sanitize_generated_text(raw_paragraph.get("text"), max_chars=1100)
            if not text:
                continue
            paragraphs.append({
                "text": text,
                "evidence_ids": _valid_evidence_ids(raw_paragraph.get("evidence_ids"), allowed),
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
    unsupported_terms = _unsupported_domain_terms(body, evidence)
    if unsupported_terms:
        grounding_errors.append(
            "termes de domaine absents des preuves : " + ", ".join(unsupported_terms)
        )
    grounding_errors.extend(_evidence_grounding_errors(paragraphs, items, evidence))
    grounding_errors.extend(_comparison_consistency_errors(body))
    grounding_errors.extend(_unsupported_target_errors(body, evidence, config.key))
    grounding_errors.extend(_truncated_term_errors(body, evidence))
    minimum_reached = (
        len(items) >= config.min_items
        if config.display_mode == "numbered_items"
        else len(paragraphs) >= config.min_items
    )
    return {
        "body": clean_text(body, 5000),
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
        body = "Information insuffisante dans les sources indexées ; cette section doit être complétée par le consultant."
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
        return {"body": body, "paragraphs": [], "items": items, "evidence_ids": used, "evidence": selected[:len(items)], "valid": bool(body)}

    paragraphs = []
    for ev in selected:
        text = sanitize_generated_text(ev.get("excerpt"), 900)
        if text:
            paragraphs.append({"text": text, "evidence_ids": [ev["evidence_id"]]})
    body = "\n\n".join(item["text"] for item in paragraphs)
    used = [eid for item in paragraphs for eid in item["evidence_ids"]]
    return {"body": body, "paragraphs": paragraphs, "items": [], "evidence_ids": used, "evidence": selected[:len(paragraphs)], "valid": bool(body)}


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
    telemetry: Dict[str, Any] = {
        "configured_max_input_tokens": config.max_input_tokens,
        "configured_max_output_tokens": config.max_output_tokens,
        "estimated_prompt_tokens": estimate_tokens(prompt),
        "prompt_chars": len(prompt),
        "evidence_count": len(evidence),
        "display_mode": config.display_mode,
    }

    if llm is None:
        result = _fallback_from_evidence(config, evidence)
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
        parsed = parse_section_result(raw, config, evidence)
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
            retry_prompt = (
                prompt
                + "\n\nCORRECTION FACTUELLE OBLIGATOIRE :\n"
                + "La réponse précédente n'a pas respecté le contrat factuel : "
                + "; ".join(str(error) for error in validation_errors)
                + ". Réécris entièrement le JSON. Chaque paragraphe ou élément doit avoir "
                  "au moins un evidence_id. Ne change ni le domaine ni la nature des objets, "
                  "ne transforme aucun résultat en objectif, et signale explicitement toute "
                  "comparaison numérique contradictoire. Utilise uniquement les PREUVES ci-dessus."
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
            retry_parsed = parse_section_result(raw_retry, config, evidence)
            telemetry["grounding_retry"] = True
            telemetry["first_validation_errors"] = validation_errors
            if retry_parsed.get("valid"):
                retry_parsed.update({
                    "status": "llm_section_json_after_grounding_retry",
                    "telemetry": telemetry,
                })
                return retry_parsed, retry_prompt

        fallback = _fallback_from_evidence(config, evidence)
        fallback.update({
            "status": "fallback_after_invalid_section_json",
            "validation_errors": parsed.get("validation_errors") or [],
            "telemetry": telemetry,
        })
        return fallback, prompt
    except Exception as exc:
        fallback = _fallback_from_evidence(config, evidence)
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
    llm_statuses = {"llm_section_json", "llm_section_json_after_grounding_retry"}
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
        "context_engineering_version": "v183_gpt_sectional_strict_evidence_and_consistency",
    }


def frascati_summary_text(frascati_summary: Dict[str, Any]) -> str:
    summary = frascati_summary if isinstance(frascati_summary, dict) else {}
    score = summary.get("average_frascati_score")
    count = summary.get("scores_count")
    decisions = summary.get("decisions_count") if isinstance(summary.get("decisions_count"), dict) else {}
    candidate_levels = summary.get("candidate_levels_count") if isinstance(summary.get("candidate_levels_count"), dict) else {}
    decision_pool_count = sum(
        int(value) for value in candidate_levels.values() if isinstance(value, (int, float))
    )
    parts: List[str] = []
    if score is not None:
        parts.append(f"Le score Frascati moyen du dossier est de {score}.")
    if count is not None:
        parts.append(
            f"Il est calculé à partir de {count} groupe(s) technique(s) évalué(s) "
            "par le module NLP/Frascati."
        )
    if decisions:
        labels = {
            "unknown": "non classé",
            "verrou_a_verifier": "passage à vérifier",
            "verrou_probable": "passage fortement indicatif",
            "non_verrou_context": "contexte non verrou",
        }
        parts.append(
            "La qualification NLP/Frascati des passages se répartit ainsi : "
            + ", ".join(
                f"{labels.get(str(key), str(key).replace('_', ' '))} : {value}"
                for key, value in decisions.items()
            )
            + "."
        )
        if decision_pool_count:
            parts.append(
                f"Ce total correspond à {decision_pool_count} passage(s) candidat(s) examinés ; "
                "il ne représente pas le nombre final de verrous consolidés."
            )
    parts.append(
        "Ce score sert à prioriser les signaux à examiner. "
        "Il ne constitue ni une validation d'éligibilité CIR ni une validation définitive des verrous."
    )
    return clean_text(" ".join(parts), 1800)


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

    # Une seule section visible : la lecture déterministe du score et la justification
    # LLM sont réunies dans « Analyse Frascati ». La clé séparée est conservée
    # uniquement pour la compatibilité API et l'audit.
    sections_by_key["lecture_frascati"] = clean_text(
        f"Lecture du score\n\n{frascati_reading}\n\n"
        f"Justification projet-spécifique\n\n{frascati_justification}",
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
        "telemetry": {
            **(
                justification_payload.get("telemetry")
                if isinstance(justification_payload.get("telemetry"), dict)
                else {}
            ),
            "display_mode": "paragraphs",
            "merged_into_analysis_frascati": True,
        },
        "status": justification_payload.get("status") or "merged_frascati_analysis",
        "valid": True,
    }

    sections_by_key["memoire_v2"] = memory_v2_usage_text(memory_v2_usage_report)
    sections_by_key["verrous_rnd"] = verrous_text_from_items(llm_reformulated_verrous or [])

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
    sections_by_title["Lecture Frascati du dossier"] = sections_by_key["lecture_frascati"]
    sections_by_title["Justification Frascati du score"] = sections_by_key["justification_frascati"]
    sections_by_title["Objectif global reformulé"] = sections_by_key["objectif_global"]
    sections_by_title["Démarche expérimentale détectée"] = sections_by_key["demarche_detectee"]
    sections_by_title["Résultats et métriques disponibles"] = sections_by_key["resultats_metriques"]
    sections_by_title["Verrous R&D / signaux de verrous"] = sections_by_key["verrous_rnd"]

    cards = build_cards(sections_by_key, payloads)
    return {
        "ok": True,
            "version": "v151_frascati_analysis_merged_evidence_contract",
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
            "version": "v151",
        },
        "format": "structured_plain_text_with_evidence",
    }
