# -*- coding: utf-8 -*-
from __future__ import annotations

"""
cir_fewshot_builder.py

Phase 3 — Few-shot Builder SAFE TEMPLATE

Rôle :
- lire style_profile_payload.json ;
- lire style_memory_payload.json uniquement pour traçabilité / statistiques ;
- générer les few-shot depuis style_profile["fewshot_templates"] ;
- ne jamais reprendre les phrases brutes Memory V2 ;
- produire un fewshot_payload propre, stable et prêt pour le prompt LLM.

Principe :
- Memory V2 = style_only ;
- Memory V2 ne sert pas de preuve ;
- Memory V2 ne doit pas être citée ;
- les faits scientifiques doivent venir uniquement des Article Cards ;
- les few-shot sont des templates génériques propres avec placeholders.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


from .cir_style_retriever import (
    clean_text,
    _read_json,
    _write_json,
    style_memory_output_path,
)

from .cir_style_profile_builder import (
    style_profile_output_path,
)


# ============================================================
# Configuration
# ============================================================

ROLE_PRIORITY = [
    "etat_art",
    "verrou",
    "limite",
    "contribution",
    "objectif",
]

ROLE_LABELS = {
    "etat_art": "État de l’art",
    "verrou": "Formulation du verrou",
    "limite": "Gap scientifique / limite",
    "contribution": "Contribution / conclusion",
    "objectif": "Objectif R&D",
}

ROLE_INPUT_HINTS = {
    "etat_art": (
        "À partir d’un verrou R&D et d’articles scientifiques sélectionnés, "
        "rédiger un état de l’art structuré, prudent et orienté limites de la littérature."
    ),
    "verrou": (
        "À partir du contexte projet, formuler un verrou scientifique ou technologique "
        "en montrant pourquoi le problème reste non trivial."
    ),
    "limite": (
        "À partir des articles disponibles, expliciter les limites de l’état de l’art "
        "et la non-transposabilité directe au cas du projet courant."
    ),
    "contribution": (
        "À partir des travaux réalisés, formuler une contribution scientifique, technique "
        "ou technologique sans reprendre les faits d’un ancien dossier."
    ),
    "objectif": (
        "À partir du contexte du dossier, formuler l’objectif R&D avec un ton consultant CIR, "
        "en distinguant l’objectif technique et l’incertitude scientifique."
    ),
}


DEFAULT_FEWSHOT_TEMPLATES = {
    "etat_art": (
        "De nombreux travaux de l’état de l’art ont exploré [famille de méthodes] pour répondre à "
        "[problématique scientifique]. Ces approches montrent l’intérêt de [principe technique], "
        "mais leur efficacité dépend fortement de [conditions expérimentales]. Ainsi, la littérature "
        "fournit des bases méthodologiques utiles, sans lever complètement les incertitudes liées à "
        "[contrainte spécifique du projet]."
    ),
    "verrou": (
        "Un verrou scientifique majeur réside dans la capacité à [objectif technique] tout en garantissant "
        "[critère de robustesse ou de performance]. Cette problématique demeure complexe, car les méthodes "
        "disponibles reposent sur des hypothèses qui ne couvrent pas totalement [conditions du projet courant]. "
        "Il est donc nécessaire de définir une approche spécifique permettant de réduire cette incertitude."
    ),
    "limite": (
        "Malgré des résultats encourageants, les approches existantes restent limitées par "
        "[limite scientifique ou méthodologique]. Leur transposition directe au cas du projet courant "
        "n’est pas immédiate, car [contrainte opérationnelle ou expérimentale]. Le gap scientifique porte "
        "donc sur la capacité à adapter, valider et objectiver ces méthodes dans un contexte plus contraint."
    ),
    "contribution": (
        "Les travaux conduits dans le cadre du projet ont apporté une contribution scientifique, technique "
        "et technologique à la problématique étudiée. Sur le plan scientifique, ils ont permis d’approfondir "
        "la compréhension de [phénomène ou mécanisme]. Sur le plan technique, ils ont structuré une démarche "
        "expérimentale permettant de comparer, valider et fiabiliser les solutions proposées dans le contexte du projet."
    ),
    "objectif": (
        "L’objectif du présent projet R&D est de développer et valider [approche technique] afin de répondre "
        "à [problématique scientifique ou technologique]. Il s’agit de dépasser les limites des approches "
        "existantes en caractérisant [incertitude principale], puis en évaluant la capacité de la solution "
        "proposée à satisfaire [critère de performance, robustesse ou généralisation]."
    ),
}


FORBIDDEN_TERMS = [
    "FICHE DESCRIPTIVE",
    "CONFIDENTIEL",
    "DU PROJET R&D",
    "PROFOND SPECIFIQUES",
    "SPÉCIFIQUES AU PROBLÈME",
    "MISE EN ŒUVRE DES TECHNIQUES",
    "MISE EN OEUVRE DES TECHNIQUES",
]

FORBIDDEN_REGEX = [
    r"\b20(1[5-9]|2[0-9]|3[0-5])\b",
    r"\.{3,}",
    r"\[\s*PAGE\s+\d+\s*\]",
    r"\bPAGE\s+\d+\b",
    r"\bIEEE\b",
    r"\bSPIE\b",
    r"\bProceedings\b",
    r"\bRemote Sensing\b",
    r"\bvol\.",
    r"\bpp\.",
    r"\b[A-Z][a-z]+ing\s+and\s+[A-Z]\.?\b",
    r"\b[A-Z]\.\s+[A-Z][a-zÀ-ÿ]+",
    r"\best\s+la\s+(profond|profonde|technique|sp[eé]cifique)\b",
    r"\bdes\s+l[’']entrainement\b",
    r"\bles\s+la\s+\w+",
    r"\bde\s+le\s+\w+",
]


# ============================================================
# Helpers
# ============================================================

def normalize_for_match(text: Any) -> str:
    s = clean_text(text).lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’", "aaaeeeeiioouuuc'")
    s = s.translate(tr)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def clean_template_text(text: Any) -> str:
    s = clean_text(text)

    if not s:
        return ""

    s = re.sub(r"\s+", " ", s)
    s = s.replace("..", ".")
    s = s.strip()

    return s


def has_placeholder(text: Any) -> bool:
    s = clean_text(text)
    return "[" in s and "]" in s


def is_forbidden_text(text: Any) -> bool:
    s = clean_text(text)
    if not s:
        return True

    low = normalize_for_match(s)

    for term in FORBIDDEN_TERMS:
        if normalize_for_match(term) in low:
            return True

    for pattern in FORBIDDEN_REGEX:
        if re.search(pattern, s, flags=re.IGNORECASE):
            return True

    if "à partir de ," in s or "a partir de ," in low:
        return True

    if "nous exploitons ici les de la littérature" in low:
        return True

    if "production de le" in low:
        return True

    if "pour que les la" in low:
        return True

    if "à exploiter des l" in low:
        return True

    return False


def validate_template(text: Any) -> Dict[str, Any]:
    s = clean_template_text(text)
    warnings = []
    score = 100

    if not s:
        return {
            "ok": False,
            "score": 0,
            "level": "weak",
            "warnings": ["Template vide."],
        }

    if len(s) < 160:
        score -= 25
        warnings.append("Template court.")

    if len(s) > 1300:
        score -= 15
        warnings.append("Template long.")

    if not has_placeholder(s):
        score -= 35
        warnings.append("Aucun placeholder détecté.")

    if is_forbidden_text(s):
        score -= 60
        warnings.append("Terme factuel, OCR ou bibliographique interdit détecté.")

    if s.endswith("..."):
        score -= 40
        warnings.append("Template tronqué avec points de suspension.")

    score = max(0, min(100, score))

    if score >= 80:
        level = "good"
    elif score >= 55:
        level = "usable"
    else:
        level = "weak"

    return {
        "ok": level != "weak",
        "score": score,
        "level": level,
        "warnings": warnings,
    }


def get_template_for_role(style_profile: Dict[str, Any], role: str) -> str:
    fewshot_templates = style_profile.get("fewshot_templates") or {}

    candidate = clean_template_text(fewshot_templates.get(role))

    quality = validate_template(candidate)
    if quality.get("ok"):
        return candidate

    fallback = DEFAULT_FEWSHOT_TEMPLATES.get(role, "")
    return clean_template_text(fallback)


def build_single_fewshot_from_template(
    role: str,
    index: int,
    style_profile: Dict[str, Any],
) -> Dict[str, Any]:
    template = get_template_for_role(style_profile, role)
    quality = validate_template(template)

    return {
        "fewshot_id": f"fewshot_{index:02d}_{role}",
        "role": role,
        "role_label": ROLE_LABELS.get(role, role),
        "input_hint": ROLE_INPUT_HINTS.get(
            role,
            "Rédiger un passage CIR en suivant uniquement le style."
        ),
        "output_style_example": template,
        "source_memory_id": "style_profile_template",
        "source_project": "",
        "source_year": "",
        "source_file": "",
        "usage": "style_only",
        "memory_as_proof": False,
        "can_be_cited": False,
        "quality": quality,
        "warning": (
            "Few-shot généré depuis style_profile['fewshot_templates']. "
            "Memory V2 n’est utilisée que pour inspirer le style global, jamais comme preuve ni comme texte copiable."
        ),
    }


def build_cir_fewshot_examples(
    style_profile: Dict[str, Any],
    max_examples: int = 5,
) -> List[Dict[str, Any]]:
    fewshots = []

    for index, role in enumerate(ROLE_PRIORITY[:max_examples], 1):
        fewshots.append(
            build_single_fewshot_from_template(
                role=role,
                index=index,
                style_profile=style_profile,
            )
        )

    return fewshots


def build_fewshot_block(
    fewshot_examples: List[Dict[str, Any]],
    style_profile: Dict[str, Any],
) -> str:
    lines = []

    lines.append("EXEMPLES FEW-SHOT DE STYLE CIR — TEMPLATES SÉCURISÉS")
    lines.append(
        "Ces exemples servent uniquement à reproduire le ton, la structure argumentative "
        "et les transitions rédactionnelles attendues dans un dossier CIR."
    )
    lines.append(
        "Ils sont génériques, contiennent des placeholders, et ne doivent jamais être cités comme sources."
    )
    lines.append(
        "Les faits scientifiques, références et citations doivent provenir uniquement des Article Cards."
    )

    paragraph_order = style_profile.get("paragraph_order") or []
    if paragraph_order:
        lines.append("\nORDRE RÉDACTIONNEL À RESPECTER :")
        for i, item in enumerate(paragraph_order, 1):
            lines.append(f"{i}. {item}")

    reasoning_patterns = style_profile.get("reasoning_patterns") or []
    comparison_patterns = style_profile.get("comparison_patterns") or []
    paragraph_blueprints = style_profile.get("paragraph_blueprints") or {}

    if reasoning_patterns:
        lines.append("\nSCHÉMAS DE RAISONNEMENT À RESPECTER :")
        for item in reasoning_patterns[:6]:
            steps = item.get("steps") or []
            label = item.get("label") or item.get("pattern_id")
            if steps:
                lines.append(f"- {label} : " + " → ".join([str(x) for x in steps]))

    if comparison_patterns:
        lines.append("\nRÈGLES DE COMPARAISON SCIENTIFIQUE :")
        for item in comparison_patterns[:6]:
            instr = item.get("writer_instruction") or item.get("label") or item.get("pattern_id")
            lines.append(f"- {instr}")

    if paragraph_blueprints:
        lines.append("\nBLUEPRINTS DE PARAGRAPHES :")
        for key, bp in list(paragraph_blueprints.items())[:8]:
            if isinstance(bp, dict):
                logic = bp.get("logic") or bp.get("paragraph_role") or ""
                if logic:
                    lines.append(f"- {key} : {logic}")

    writing_rules = style_profile.get("writing_rules") or []
    if writing_rules:
        lines.append("\nRÈGLES DE STYLE :")
        for rule in writing_rules[:10]:
            lines.append(f"- {rule}")

    tone_details = style_profile.get("tone_details") or []
    if tone_details:
        lines.append("\nTON ATTENDU :")
        for tone in tone_details[:8]:
            lines.append(f"- {tone}")

    lines.append("\nEXEMPLES FEW-SHOT :")

    for i, ex in enumerate(fewshot_examples, 1):
        lines.append(f"\n[FEWSHOT {i} — {ex.get('role_label')}]")
        lines.append("Entrée type :")
        lines.append(ex.get("input_hint") or "")
        lines.append("Sortie attendue uniquement comme style :")
        lines.append(ex.get("output_style_example") or "")
        lines.append(
            "Règle : remplacer les placeholders par les informations du dossier courant "
            "et des Article Cards ; ne jamais inventer de références ; ne jamais citer Memory V2."
        )

    return "\n".join(lines).strip()


def fewshot_quality(fewshot_examples: List[Dict[str, Any]]) -> Dict[str, Any]:
    warnings = []
    score = 0

    count = len(fewshot_examples)
    roles = {ex.get("role") for ex in fewshot_examples}

    if count >= 5:
        score += 30
    elif count >= 3:
        score += 20
    else:
        warnings.append("Peu d'exemples few-shot générés.")

    required_roles = {"etat_art", "verrou", "limite", "contribution", "objectif"}
    missing_roles = sorted(required_roles - roles)

    if not missing_roles:
        score += 30
    else:
        warnings.append(f"Rôles few-shot manquants : {missing_roles}")

    dirty_count = 0
    weak_count = 0
    no_placeholder_count = 0

    for ex in fewshot_examples:
        text = ex.get("output_style_example") or ""
        quality = ex.get("quality") or {}

        if quality.get("level") == "weak":
            weak_count += 1

        if is_forbidden_text(text):
            dirty_count += 1

        if not has_placeholder(text):
            no_placeholder_count += 1

    if dirty_count == 0:
        score += 20
    else:
        warnings.append(f"{dirty_count} few-shot(s) contiennent encore du bruit ou des faits interdits.")

    if weak_count == 0:
        score += 10
    else:
        warnings.append(f"{weak_count} few-shot(s) faibles.")

    if no_placeholder_count == 0:
        score += 10
    else:
        warnings.append(f"{no_placeholder_count} few-shot(s) sans placeholder.")

    score = max(0, min(100, score))

    if score >= 85:
        level = "good"
    elif score >= 60:
        level = "usable"
    else:
        level = "weak"

    return {
        "score": score,
        "level": level,
        "warnings": warnings,
        "roles_covered": sorted(list(roles)),
        "fewshot_count": count,
        "dirty_count": dirty_count,
        "weak_count": weak_count,
        "no_placeholder_count": no_placeholder_count,
    }


def memory_summary(style_memory: List[Dict[str, Any]]) -> Dict[str, Any]:
    roles = {}
    projects = {}
    years = {}

    for ex in style_memory:
        if not isinstance(ex, dict):
            continue

        role = clean_text(ex.get("style_role") or ex.get("role") or ex.get("target_role") or "unknown")
        project = clean_text(ex.get("project") or "unknown")
        year = clean_text(ex.get("year") or "unknown")

        roles[role] = roles.get(role, 0) + 1
        projects[project] = projects.get(project, 0) + 1
        years[year] = years.get(year, 0) + 1

    return {
        "style_memory_count": len(style_memory),
        "roles": roles,
        "projects": projects,
        "years": years,
        "raw_memory_examples_used_in_fewshot": False,
    }


# ============================================================
# API publique
# ============================================================

def fewshot_output_path(organisme: str, project: str, year: str) -> Path:
    return style_memory_output_path(organisme, project, year).parent / "fewshot_payload.json"


def build_cir_fewshot_payload(
    organisme: str,
    project: str,
    year: str,
    style_memory_payload_path: Optional[str | Path] = None,
    style_profile_payload_path: Optional[str | Path] = None,
    output_path: Optional[str | Path] = None,
    max_examples: int = 5,
) -> Dict[str, Any]:
    """
    Lit style_profile_payload.json et produit fewshot_payload.json.

    Important :
    style_memory_payload.json est lu uniquement pour traçabilité.
    Aucun texte brut Memory V2 n’est injecté dans fewshot_examples.
    """

    memory_path = (
        Path(style_memory_payload_path)
        if style_memory_payload_path
        else style_memory_output_path(organisme, project, year)
    )

    profile_path = (
        Path(style_profile_payload_path)
        if style_profile_payload_path
        else style_profile_output_path(organisme, project, year)
    )

    out_path = (
        Path(output_path)
        if output_path
        else fewshot_output_path(organisme, project, year)
    )

    memory_payload = _read_json(memory_path, {}) or {}
    profile_payload = _read_json(profile_path, {}) or {}

    style_memory = memory_payload.get("style_memory") or memory_payload.get("style_examples") or []
    style_memory = [ex for ex in style_memory if isinstance(ex, dict)]

    style_profile = profile_payload.get("style_profile") or {}

    if not style_profile:
        result = {
            "ok": False,
            "phase": "phase_3_dynamic_fewshot_style",
            "step": "cir_fewshot_builder",
            "status": "empty_style_profile",
            "message": "Aucun style_profile trouvé. Lance d'abord cir_style_profile_builder.py.",
            "style_memory_payload_path": str(memory_path),
            "style_profile_payload_path": str(profile_path),
            "fewshot_examples": [],
            "fewshot_block": "",
            "quality": {
                "score": 0,
                "level": "weak",
                "warnings": ["style_profile absent."],
            },
            "output_path": str(out_path),
        }
        _write_json(out_path, result)
        return result

    fewshot_examples = build_cir_fewshot_examples(
        style_profile=style_profile,
        max_examples=max_examples,
    )

    fewshot_block = build_fewshot_block(
        fewshot_examples=fewshot_examples,
        style_profile=style_profile,
    )

    quality = fewshot_quality(fewshot_examples)

    result = {
        "ok": True,
        "phase": "phase_3_dynamic_fewshot_style",
        "step": "cir_fewshot_builder",
        "payload_type": "fewshot_payload_v3_reasoning_patterns",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "organisme": organisme,
        "project": project,
        "year": str(year),
        "style_memory_payload_path": str(memory_path),
        "style_profile_payload_path": str(profile_path),
        "style_profile_type": style_profile.get("profile_type"),
        "style_profile_strategy": style_profile.get("profile_strategy"),
        "fewshot_count": len(fewshot_examples),
        "fewshot_examples": fewshot_examples,
        "fewshot_block": fewshot_block,
        "quality": quality,
        "memory_summary": memory_summary(style_memory),
        "rules": {
            "usage": "style_only",
            "memory_as_proof": False,
            "can_be_cited": False,
            "scientific_sources_allowed": "article_cards_only",
            "must_not_copy_historical_facts": True,
            "must_not_cite_memory": True,
            "raw_memory_examples_used_in_fewshot": False,
            "fewshots_generated_from_style_profile_templates": True,
            "reasoning_patterns_included": bool(style_profile.get("reasoning_patterns")),
            "comparison_patterns_included": bool(style_profile.get("comparison_patterns")),
            "paragraph_blueprints_included": bool(style_profile.get("paragraph_blueprints")),
            "placeholders_must_be_filled_from_current_project_and_article_cards": True,
        },
        "output_path": str(out_path),
    }

    _write_json(out_path, result)
    return result
