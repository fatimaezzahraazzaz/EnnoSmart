# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, List


def _clean(value: Any) -> str:
    return str(value or "").strip()


def build_sources_block(sources: List[Dict[str, Any]], max_chars_per_source: int = 1500) -> str:
    if not sources:
        return "Aucune source disponible."

    blocks = []

    for i, src in enumerate(sources, start=1):
        meta = src.get("metadata", {}) or {}
        text = _clean(src.get("text"))

        if len(text) > max_chars_per_source:
            text = text[:max_chars_per_source].rsplit(" ", 1)[0] + "..."

        blocks.append(
            f"""[SOURCE {i}]
Rôle : {_clean(meta.get("role"))}
Rôle final : {_clean(meta.get("final_role"))}
Décision Frascati : {_clean(meta.get("frascati_decision"))}
Score Frascati : {_clean(meta.get("frascati_score"))}
Document : {_clean(meta.get("document"))}
Section : {_clean(meta.get("section_title"))}
Thème : {_clean(meta.get("theme_label"))}
Contenu :
{text}
"""
        )

    return "\n\n".join(blocks)


def build_chat_answer_prompt(question: str, sources: List[Dict[str, Any]]) -> str:
    sources_block = build_sources_block(sources)

    return f"""
Tu es EnnoAmel, assistant du système EnnoSmart.

Tu réponds uniquement à partir des sources fournies.
Tu ne dois pas inventer.
Si les sources sont insuffisantes, dis-le clairement.
Tu dois distinguer objectif, verrou, méthode, résultat et point à valider.

Question :
{question}

Sources :
{sources_block}

Réponse :
""".strip()


def build_diagnostic_section_prompt(section: str, sources: List[Dict[str, Any]], frascati_summary: Dict[str, Any] | None = None) -> str:
    sources_block = build_sources_block(sources)
    frascati_summary = frascati_summary or {}

    return f"""
Tu es EnnoDiagnostic, agent d'analyse CIR.

Section demandée : {section}

Règles :
- Utilise uniquement les sources.
- Ne conclus pas définitivement à l'éligibilité CIR.
- Ne transforme pas une méthode ou un paramètre en verrou.
- Les verrous explicites sont prioritaires sur les verrous implicites.
- Les verrous implicites sont des hypothèses à valider consultant.
- Chaque affirmation importante doit rester reliée aux sources.

Résumé Frascati :
{frascati_summary}

Sources :
{sources_block}

Rédige uniquement la section : {section}
""".strip()


def build_full_diagnostic_prompt(sources: List[Dict[str, Any]], frascati_summary: Dict[str, Any] | None = None) -> str:
    sources_block = build_sources_block(sources, max_chars_per_source=1800)
    frascati_summary = frascati_summary or {}

    return f"""
Tu es EnnoDiagnostic, agent d'analyse CIR du système EnnoSmart.

Tu dois produire un pré-diagnostic structuré à partir des sources RAG et du résumé Frascati.
Le LLM ne décide pas les verrous : il reformule les signaux issus du NLP + FrascatiGuard.

Règles obligatoires :
- Utilise uniquement les sources.
- Ne pas inventer.
- Ne pas conclure définitivement à l'éligibilité CIR.
- Distingue : objectif, verrous, méthode, résultats, points à valider.
- Les verrous explicites sont prioritaires.
- Les verrous implicites doivent rester "à valider consultant".
- Ne transforme jamais une norme, un test, une méthode, un paramètre ou un résultat en verrou principal sans incertitude technique.

Résumé Frascati :
{frascati_summary}

Sources :
{sources_block}

Format attendu :

## Résumé stratégique
...

## Objectif global
...

## Verrous R&D potentiels à valider consultant
1. ...
2. ...

## Démarche expérimentale détectée
...

## Résultats et métriques détectés
...

## Points à valider
...
""".strip()
