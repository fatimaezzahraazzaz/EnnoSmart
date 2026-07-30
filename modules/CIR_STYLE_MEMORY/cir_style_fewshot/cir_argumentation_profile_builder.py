# -*- coding: utf-8 -*-
from __future__ import annotations

"""
cir_argumentation_profile_builder.py

Phase 3.5 — CIR Argumentation Profile Builder SAFE

Rôle :
- construire un profil d'argumentation CIR à partir des signaux de style extraits ;
- capturer la logique rédactionnelle d'un consultant CIR :
    littérature -> insuffisances -> limites -> non-transposabilité -> verrou -> travaux R&D ;
- ne jamais copier les phrases brutes Memory V2 ;
- ne jamais utiliser Memory V2 comme preuve scientifique ;
- fournir à Phase 4.5 et Phase 5 un schéma de raisonnement robuste.

Important :
- ce module ne produit pas l'état de l'art ;
- ce module ne cite aucun article ;
- ce module ne remplace pas Article Cards ;
- il fournit uniquement une structure argumentative.
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

from .cir_style_extractor import (
    style_extraction_output_path,
)

from .cir_style_profile_builder import (
    style_profile_output_path,
)


# ============================================================
# Templates argumentatifs CIR sûrs
# ============================================================

CONSULTANT_REASONING_FLOW = [
    {
        "step": 1,
        "name": "positionnement_scientifique",
        "goal": "Situer le verrou dans son contexte scientifique et technique.",
        "writer_instruction": (
            "Commencer par la problématique scientifique, pas par les articles. "
            "Expliquer pourquoi le sujet est important pour le projet courant."
        ),
    },
    {
        "step": 2,
        "name": "travaux_existants_par_familles",
        "goal": "Présenter les familles de travaux existants.",
        "writer_instruction": (
            "Regrouper les articles par familles d'approches. "
            "Ne jamais écrire article par article."
        ),
    },
    {
        "step": 3,
        "name": "insuffisances_persistantes",
        "goal": "Identifier ce que l'état de l'art ne résout pas.",
        "writer_instruction": (
            "Formuler les insuffisances sous forme de limites scientifiques, "
            "méthodologiques ou expérimentales."
        ),
    },
    {
        "step": 4,
        "name": "non_transposabilite_projet",
        "goal": "Expliquer pourquoi les travaux existants ne sont pas directement transposables.",
        "writer_instruction": (
            "Relier les limites de la littérature aux contraintes propres du dossier courant."
        ),
    },
    {
        "step": 5,
        "name": "verrou_scientifique",
        "goal": "Formuler le gap scientifique ou technique.",
        "writer_instruction": (
            "Montrer l'écart entre ce qui est disponible dans la littérature "
            "et ce qui doit être validé dans le projet."
        ),
    },
    {
        "step": 6,
        "name": "necessite_travaux_rd",
        "goal": "Justifier la nécessité de travaux R&D.",
        "writer_instruction": (
            "Conclure sur la nécessité d'une démarche expérimentale propre au projet."
        ),
    },
]


INSUFFICIENCY_TAXONOMY = [
    {
        "code": "robustesse_limitee",
        "label": "Manque de robustesse",
        "template": (
            "Malgré des résultats encourageants, les approches existantes restent limitées "
            "lorsqu'elles sont confrontées à [conditions variables, bruit, cas extrêmes ou données rares]."
        ),
        "use_when": "Les articles montrent des résultats mais sans garantie forte de robustesse.",
    },
    {
        "code": "generalisation_limitee",
        "label": "Généralisation limitée",
        "template": (
            "Les performances rapportées dans la littérature dépendent fortement de "
            "[jeu de données, protocole, hypothèse ou configuration expérimentale], "
            "ce qui limite leur généralisation au contexte du projet."
        ),
        "use_when": "Les travaux sont validés sur des benchmarks ou contextes trop spécifiques.",
    },
    {
        "code": "validation_incomplete",
        "label": "Validation expérimentale incomplète",
        "template": (
            "Les travaux identifiés ne démontrent pas complètement la capacité de la méthode "
            "à satisfaire [critère de performance, robustesse ou représentativité] "
            "dans les conditions réelles du projet."
        ),
        "use_when": "Les preuves expérimentales ne couvrent pas les contraintes du dossier.",
    },
    {
        "code": "transposition_non_immediate",
        "label": "Non-transposabilité directe",
        "template": (
            "La transposition directe de ces approches au cas du projet courant n'est pas immédiate, "
            "car [contrainte opérationnelle, nature des données, protocole ou environnement cible] "
            "diffère des conditions étudiées."
        ),
        "use_when": "Les articles sont utiles mais pas directement applicables.",
    },
    {
        "code": "dependance_aux_donnees",
        "label": "Dépendance aux données",
        "template": (
            "Ces approches restent dépendantes de [quantité, qualité, représentativité ou annotation des données], "
            "ce qui maintient une incertitude sur leur performance dans le contexte du projet."
        ),
        "use_when": "Le verrou dépend de données limitées, bruitées ou peu représentatives.",
    },
    {
        "code": "criteres_evaluation_insuffisants",
        "label": "Critères d'évaluation insuffisants",
        "template": (
            "Les critères d'évaluation utilisés dans la littérature ne suffisent pas toujours "
            "à caractériser la pertinence de la solution au regard des contraintes du projet."
        ),
        "use_when": "Les métriques existantes ne couvrent pas tous les enjeux du projet.",
    },
    {
        "code": "complexite_integration",
        "label": "Complexité d'intégration",
        "template": (
            "Même lorsque les approches sont pertinentes sur le plan méthodologique, "
            "leur intégration dans un pipeline complet soulève des difficultés de mise en œuvre, "
            "de reproductibilité ou de passage à l'échelle."
        ),
        "use_when": "Le projet nécessite une chaîne complète et pas seulement une méthode isolée.",
    },
]


CONSULTANT_SECTION_BLUEPRINTS = {
    "positionnement_scientifique_du_verrou": {
        "objective": "Présenter le problème scientifique sans commencer par les articles.",
        "must_include": [
            "contexte scientifique du verrou",
            "lien avec l'objectif R&D",
            "incertitude principale",
        ],
        "must_avoid": [
            "énumération d'articles",
            "phrases du type : l'article A1 présente",
            "affirmation sans lien avec le verrou",
        ],
        "template": (
            "Le verrou étudié s'inscrit dans une problématique de [domaine scientifique], "
            "où l'objectif est de [objectif technique] tout en garantissant "
            "[critère de robustesse, performance ou généralisation]. "
            "La difficulté ne réside pas uniquement dans l'application d'une méthode existante, "
            "mais dans sa capacité à répondre aux contraintes spécifiques du projet."
        ),
    },
    "travaux_existants_directement_lies": {
        "objective": "Synthétiser les travaux directs par familles d'approches.",
        "must_include": [
            "familles de méthodes",
            "apports connus",
            "citations en fin de phrase",
        ],
        "must_avoid": [
            "article par article",
            "titres d'articles dans le corps du texte",
            "citations comme sujet grammatical",
        ],
        "template": (
            "Les travaux directement liés mettent en évidence plusieurs familles d'approches permettant de "
            "[apport scientifique principal]. Une première famille repose sur [principe technique 1], "
            "tandis qu'une seconde explore [principe technique 2]. Ces contributions fournissent un socle "
            "méthodologique utile pour cadrer le verrou, sans toutefois démontrer leur suffisance dans "
            "les conditions du projet. [citations]"
        ),
    },
    "travaux_connexes_ou_methodes_transposables": {
        "objective": "Utiliser les articles connexes comme éclairage méthodologique, pas comme preuves centrales.",
        "must_include": [
            "approches proches",
            "apports méthodologiques",
            "prudence sur la transposition",
        ],
        "must_avoid": [
            "description du domaine applicatif éloigné",
            "présentation individuelle des sources connexes",
            "survalorisation des articles connexes",
        ],
        "template": (
            "Des travaux connexes apportent un éclairage méthodologique complémentaire sur "
            "[principe général], notamment en matière de [classification, robustesse, généralisation ou évaluation]. "
            "Toutefois, ces contributions relèvent de contextes applicatifs distincts et leur transposition "
            "au cas du projet doit être discutée avec prudence. [citations]"
        ),
    },
    "limites_de_l_etat_de_l_art": {
        "objective": "Formuler les insuffisances de la littérature.",
        "must_include": [
            "limites scientifiques",
            "limites méthodologiques",
            "limites expérimentales",
        ],
        "must_avoid": [
            "jugement vague",
            "simple répétition du gap",
            "absence de lien avec le projet",
        ],
        "template": (
            "Malgré les avancées récentes, plusieurs insuffisances persistent. "
            "Les résultats disponibles restent fortement dépendants de [conditions de validation], "
            "tandis que la représentativité de [données, scénarios ou configurations] n'est pas totalement établie. "
            "Ces limites empêchent de conclure directement sur la robustesse de la solution dans le contexte du projet."
        ),
    },
    "gap_scientifique_technique": {
        "objective": "Exprimer clairement l'écart entre littérature et besoin projet.",
        "must_include": [
            "écart littérature/projet",
            "incertitude restante",
            "besoin de validation propre",
        ],
        "must_avoid": [
            "gap trop générique",
            "répétition article par article",
            "absence de contrainte projet",
        ],
        "template": (
            "Le gap scientifique réside dans l'écart entre les conditions étudiées dans la littérature "
            "et les contraintes spécifiques du dossier courant. Les travaux existants permettent de cadrer "
            "la problématique, mais ne suffisent pas à démontrer [performance, robustesse ou généralisation] "
            "dans [contexte spécifique]."
        ),
    },
    "synthese_cir_exploitable": {
        "objective": "Conclure sur la nécessité de travaux R&D.",
        "must_include": [
            "nécessité d'expérimenter",
            "adaptation au contexte",
            "validation spécifique",
        ],
        "must_avoid": [
            "conclusion promotionnelle",
            "affirmation de réussite non prouvée",
            "citation de Memory V2",
        ],
        "template": (
            "Ces limites justifient la mise en œuvre de travaux R&D spécifiques. "
            "La démarche du projet doit permettre de tester, adapter et valider les approches identifiées "
            "dans un contexte contraint, afin de réduire les incertitudes relatives à "
            "[critère principal de validation]."
        ),
    },
}


CIR_CONNECTORS = [
    "Malgré ces avancées,",
    "Toutefois,",
    "Cependant,",
    "En outre,",
    "Par ailleurs,",
    "Pour l'ensemble de ces raisons,",
    "Ainsi,",
    "Dans ce contexte,",
    "Ces limites justifient",
    "La transposition directe n'est donc pas immédiate",
]


ANTI_ARTICLE_BY_ARTICLE_RULES = [
    "Ne jamais commencer une phrase par une citation.",
    "Ne jamais écrire : l'article [A1] présente.",
    "Ne jamais écrire : les articles [A1], [A2] montrent.",
    "Ne jamais utiliser un titre d'article dans le corps du texte.",
    "Regrouper les sources par familles d'approches.",
    "Placer les citations seulement en fin de phrase ou de paragraphe.",
    "Décrire les articles connexes de manière abstraite si leur domaine est éloigné.",
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


def argumentation_profile_output_path(organisme: str, project: str, year: str) -> Path:
    return style_memory_output_path(organisme, project, year).parent / "argumentation_profile_payload.json"


def detect_available_signals(extraction_payload: Dict[str, Any]) -> Dict[str, Any]:
    extraction = extraction_payload.get("style_extraction") or {}

    transition_patterns = extraction.get("transition_patterns") or []
    gap_patterns = extraction.get("gap_patterns") or []
    conclusion_patterns = extraction.get("conclusion_patterns") or []

    joined = normalize_for_match(
        " ".join(transition_patterns + gap_patterns + conclusion_patterns)
    )

    signals = {
        "contrast_markers_detected": [],
        "gap_markers_detected": [],
        "conclusion_markers_detected": [],
        "has_argumentative_style_memory": False,
    }

    for marker in [
        "malgre",
        "toutefois",
        "cependant",
        "en outre",
        "par ailleurs",
        "ainsi",
        "en conclusion",
        "pour l'ensemble de ces raisons",
    ]:
        if marker in joined:
            signals["contrast_markers_detected"].append(marker)

    for marker in [
        "limite",
        "insuffisance",
        "incertitude",
        "non transposable",
        "necessite",
        "verrou",
        "complexe",
    ]:
        if marker in joined:
            signals["gap_markers_detected"].append(marker)

    for marker in [
        "en conclusion",
        "ces limites justifient",
        "travaux r&d",
        "contribution",
    ]:
        if marker in joined:
            signals["conclusion_markers_detected"].append(marker)

    total = (
        len(signals["contrast_markers_detected"])
        + len(signals["gap_markers_detected"])
        + len(signals["conclusion_markers_detected"])
    )

    signals["has_argumentative_style_memory"] = total >= 3

    return signals


def build_argumentation_block(argumentation_profile: Dict[str, Any]) -> str:
    lines: List[str] = []

    lines.append("PROFIL D'ARGUMENTATION CIR — À UTILISER POUR LE RAISONNEMENT")
    lines.append(
        "Ce bloc décrit la logique argumentative attendue. "
        "Il ne contient aucune preuve scientifique et ne doit jamais être cité."
    )

    lines.append("\nFLUX DE RAISONNEMENT CONSULTANT :")
    for item in argumentation_profile.get("consultant_reasoning_flow") or []:
        lines.append(f"{item.get('step')}. {item.get('name')} — {item.get('goal')}")

    reasoning_patterns = argumentation_profile.get("reasoning_patterns") or []
    if reasoning_patterns:
        lines.append("\nSCHÉMAS DE RAISONNEMENT À PRIVILÉGIER :")
        for item in reasoning_patterns[:6]:
            steps = item.get("steps") or []
            lines.append(f"- {item.get('label') or item.get('pattern_id')} : " + " → ".join([str(x) for x in steps]))

    lines.append("\nLOGIQUE D'INSUFFISANCE À PRIVILÉGIER :")
    for item in argumentation_profile.get("insufficiency_taxonomy") or []:
        lines.append(f"- {item.get('label')} : {item.get('template')}")

    lines.append("\nRÈGLES ANTI-FICHE ARTICLE :")
    for rule in argumentation_profile.get("anti_article_by_article_rules") or []:
        lines.append(f"- {rule}")

    lines.append("\nCONNECTEURS CIR RECOMMANDÉS :")
    cleaned_connectors = [
        str(x).strip().rstrip(",")
        for x in (argumentation_profile.get("cir_connectors") or [])
        if str(x).strip()
    ]
    lines.append(", ".join(cleaned_connectors))

    lines.append("\nRÈGLE MAJEURE :")
    lines.append(
        "Le writer doit raisonner à partir du verrou et des insuffisances scientifiques. "
        "Les articles ne servent qu'à justifier les phrases par des citations en fin de phrase."
    )

    return "\n".join(lines).strip()


def score_argumentation_profile(
    extraction_payload: Dict[str, Any],
    argumentation_profile: Dict[str, Any],
) -> Dict[str, Any]:
    warnings = []
    score = 0

    signals = argumentation_profile.get("detected_signals") or {}

    if signals.get("has_argumentative_style_memory"):
        score += 20
    else:
        warnings.append("Peu de signaux argumentatifs détectés dans Memory V2, profil canonique utilisé.")

    if len(argumentation_profile.get("consultant_reasoning_flow") or []) >= 6:
        score += 25
    else:
        warnings.append("Flux de raisonnement incomplet.")

    if len(argumentation_profile.get("insufficiency_taxonomy") or []) >= 6:
        score += 25
    else:
        warnings.append("Taxonomie d'insuffisances faible.")

    if len(argumentation_profile.get("section_blueprints") or {}) >= 5:
        score += 20
    else:
        warnings.append("Blueprints de sections incomplets.")

    if argumentation_profile.get("anti_article_by_article_rules"):
        score += 10
    else:
        warnings.append("Règles anti article-par-article absentes.")

    if argumentation_profile.get("reasoning_patterns") and argumentation_profile.get("paragraph_blueprints"):
        score += 10
    else:
        warnings.append("Patterns de raisonnement / blueprints rhétoriques absents.")

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
    }




def _profile_rhetorical_memory(style_profile_payload: Dict[str, Any]) -> Dict[str, Any]:
    profile = style_profile_payload.get("style_profile") or {}
    if not isinstance(profile, dict):
        profile = {}
    return {
        "reasoning_patterns": profile.get("reasoning_patterns") or [],
        "comparison_patterns": profile.get("comparison_patterns") or [],
        "scientific_moves": profile.get("scientific_moves") or [],
        "paragraph_blueprints": profile.get("paragraph_blueprints") or {},
        "usage": "rhetorical_structure_only",
        "memory_as_proof": False,
        "can_be_cited": False,
    }

# ============================================================
# API publique
# ============================================================

def build_argumentation_profile_payload(
    organisme: str,
    project: str,
    year: str,
    style_extraction_payload_path: Optional[str | Path] = None,
    style_profile_payload_path: Optional[str | Path] = None,
    output_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """
    Produit argumentation_profile_payload.json.

    Ce payload sera utilisé ensuite par :
    - Phase 4.5 Scientific Reasoning Builder ;
    - Phase 5 Writer CIR.
    """

    extraction_path = (
        Path(style_extraction_payload_path)
        if style_extraction_payload_path
        else style_extraction_output_path(organisme, project, year)
    )

    profile_path = (
        Path(style_profile_payload_path)
        if style_profile_payload_path
        else style_profile_output_path(organisme, project, year)
    )

    out_path = (
        Path(output_path)
        if output_path
        else argumentation_profile_output_path(organisme, project, year)
    )

    extraction_payload = _read_json(extraction_path, {}) or {}
    style_profile_payload = _read_json(profile_path, {}) or {}

    detected_signals = detect_available_signals(extraction_payload)
    rhetorical_memory = _profile_rhetorical_memory(style_profile_payload)

    argumentation_profile = {
        "profile_type": "cir_argumentation_profile_v2_reasoning_patterns",
        "profile_strategy": "canonical_cir_reasoning_plus_rhetorical_memory",
        "consultant_reasoning_flow": CONSULTANT_REASONING_FLOW,
        "insufficiency_taxonomy": INSUFFICIENCY_TAXONOMY,
        "section_blueprints": CONSULTANT_SECTION_BLUEPRINTS,
        "rhetorical_memory": rhetorical_memory,
        "reasoning_patterns": rhetorical_memory.get("reasoning_patterns") or [],
        "comparison_patterns": rhetorical_memory.get("comparison_patterns") or [],
        "scientific_moves": rhetorical_memory.get("scientific_moves") or [],
        "paragraph_blueprints": rhetorical_memory.get("paragraph_blueprints") or {},
        "cir_connectors": CIR_CONNECTORS,
        "anti_article_by_article_rules": ANTI_ARTICLE_BY_ARTICLE_RULES,
        "detected_signals": detected_signals,
        "style_profile_summary": {
            "available": bool(style_profile_payload),
            "payload_type": style_profile_payload.get("payload_type"),
            "style_profile_type": (
                (style_profile_payload.get("style_profile") or {}).get("profile_type")
                if isinstance(style_profile_payload.get("style_profile"), dict)
                else None
            ),
            "quality": (
                (style_profile_payload.get("style_profile") or {}).get("quality")
                if isinstance(style_profile_payload.get("style_profile"), dict)
                else None
            ),
        },
        "writer_logic": {
            "main_principle": (
                "Le writer doit partir du verrou et des insuffisances de l'état de l'art, "
                "pas des articles."
            ),
            "article_usage": (
                "Les articles servent uniquement d'appuis scientifiques citables. "
                "Ils ne doivent jamais devenir le sujet grammatical du texte."
            ),
            "direct_articles_usage": (
                "Les articles Direct doivent soutenir les familles d'approches centrales."
            ),
            "related_articles_usage": (
                "Les articles Connexes doivent soutenir la discussion méthodologique ou la non-transposabilité, "
                "sans décrire leur domaine applicatif éloigné."
            ),
            "memory_usage": (
                "Memory V2 sert uniquement au style, au ton et au schéma argumentatif. "
                "Elle ne fournit aucune preuve scientifique."
            ),
        },
        "output_expectations_for_phase_5": {
            "expected_tone": "consultant CIR senior",
            "expected_structure": [
                "positionnement scientifique",
                "familles de travaux existants",
                "insuffisances persistantes",
                "limites de transposition",
                "gap scientifique",
                "synthèse CIR exploitable",
            ],
            "citation_style": "citations uniquement en fin de phrase ou paragraphe",
            "forbidden_style": "fiche article ou liste descriptive d'articles",
        },
        "rules": {
            "usage": "argumentation_style_only",
            "memory_as_proof": False,
            "can_be_cited": False,
            "scientific_sources_allowed": "article_cards_only",
            "raw_memory_sentences_injected": False,
            "historical_facts_copied": False,
            "safe_for_phase_5": True,
        },
    }

    quality = score_argumentation_profile(
        extraction_payload=extraction_payload,
        argumentation_profile=argumentation_profile,
    )

    argumentation_profile["quality"] = quality

    argumentation_block = build_argumentation_block(argumentation_profile)

    result = {
        "ok": True,
        "phase": "phase_3_dynamic_fewshot_style",
        "step": "cir_argumentation_profile_builder",
        "payload_type": "argumentation_profile_payload_v2_reasoning_patterns",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "organisme": organisme,
        "project": project,
        "year": str(year),
        "input_paths": {
            "style_extraction_payload": str(extraction_path),
            "style_profile_payload": str(profile_path),
        },
        "argumentation_profile": argumentation_profile,
        "argumentation_block": argumentation_block,
        "quality": quality,
        "rules": {
            "usage": "argumentation_style_only",
            "memory_as_proof": False,
            "can_be_cited": False,
            "scientific_sources_allowed": "article_cards_only",
            "raw_memory_sentences_injected": False,
            "historical_facts_copied": False,
        },
        "output_path": str(out_path),
    }

    _write_json(out_path, result)
    return result