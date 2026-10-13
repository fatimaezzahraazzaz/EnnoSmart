# -*- coding: utf-8 -*-
from __future__ import annotations

"""Prompts RAG communs.

Le diagnostic principal V140 utilise `diagnostic_static_presenter.py`.
Ce module reste utile pour le chat, les routes historiques et les tests.
"""

import json
from typing import Any, Dict, List


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _evidence_id(source: Dict[str, Any], index: int) -> str:
    meta = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    return _clean(meta.get("rag_chunk_id") or source.get("id") or f"E{index}")


def build_evidence_catalog(
    sources: List[Dict[str, Any]],
    max_sources: int = 10,
    max_chars_per_source: int = 900,
) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    seen = set()
    for index, source in enumerate(sources or [], start=1):
        if not isinstance(source, dict):
            continue
        meta = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        text = _clean(source.get("text") or source.get("source_text") or source.get("excerpt"))
        if not text:
            continue
        signature = (_clean(meta.get("document")), " ".join(text[:260].lower().split()))
        if signature in seen:
            continue
        seen.add(signature)
        output.append({
            "evidence_id": f"E{len(output) + 1}",
            "rag_chunk_id": _evidence_id(source, index),
            "role": _clean(meta.get("role")),
            "final_role": _clean(meta.get("final_role")),
            "frascati_decision": _clean(meta.get("frascati_decision")),
            "frascati_score": meta.get("frascati_score"),
            "text": text[:max_chars_per_source],
        })
        if len(output) >= max_sources:
            break
    return output


def build_sources_block(sources: List[Dict[str, Any]], max_chars_per_source: int = 900) -> str:
    evidence = build_evidence_catalog(
        sources,
        max_sources=max(1, len(sources or [])),
        max_chars_per_source=max_chars_per_source,
    )
    if not evidence:
        return "[]"
    return json.dumps(evidence, ensure_ascii=False, indent=2)


def build_chat_answer_prompt(question: str, sources: List[Dict[str, Any]]) -> str:
    return f"""
Tu es EnnoAmel, assistant du système EnnoSmart.

Réponds uniquement à partir des preuves fournies. N'invente rien.
Si elles sont insuffisantes, indique précisément ce qui manque.
Dans la réponse visible, n'affiche aucun chemin local et ne recopie pas un nom de fichier à répétition.
Place les références utilisées à la fin sous la forme `Références : E1, E3`.

Question :
{_clean(question)}

Preuves :
{build_sources_block(sources)}
""".strip()


def _section_schema(section: str) -> Dict[str, Any]:
    normalized = _clean(section).lower()
    if any(word in normalized for word in ("démarche", "demarche", "paramètre", "parametre", "valider")):
        return {
            "items": [
                {"label": "Point 1", "text": "Explication complète", "evidence_ids": ["E1"]}
            ]
        }
    return {
        "paragraphs": [
            {"text": "Paragraphe explicatif", "evidence_ids": ["E1"]}
        ]
    }


def build_diagnostic_section_prompt(
    section: str,
    sources: List[Dict[str, Any]],
    frascati_summary: Dict[str, Any] | None = None,
) -> str:
    return f"""
Tu es EnnoDiagnostic, agent d'analyse CIR.

Rédige uniquement la section « {_clean(section)} ».
Retourne seulement un JSON valide conforme à ce schéma :
{json.dumps(_section_schema(section), ensure_ascii=False, indent=2)}

Contraintes :
- faits provenant uniquement des preuves ;
- aucun Markdown ;
- aucun nom de fichier dans le texte visible ;
- aucune ligne `Source`, `Indice source` ou `Dans le document` ;
- les liens entre rédaction et preuves sont uniquement dans `evidence_ids` ;
- ne recalcule pas Frascati ;
- ne présente aucune décision CIR comme validée.

Résumé Frascati déjà calculé :
{json.dumps(frascati_summary or {}, ensure_ascii=False)}

Preuves :
{build_sources_block(sources)}
""".strip()


def build_full_diagnostic_prompt(
    sources: List[Dict[str, Any]],
    frascati_summary: Dict[str, Any] | None = None,
) -> str:
    """Compatibilité historique.

    Pour des budgets stricts, préférer un appel par section via
    `diagnostic_static_presenter.generate_structured_diagnostic_core`.
    """
    template = {
        "synthese_strategique": "",
        "objectif_global": "",
        "demarche_detectee": "",
        "resultats_metriques": "",
        "parametres_contraintes": "",
    }
    return f"""
Tu es EnnoDiagnostic. Retourne uniquement ce JSON, avec les mêmes clés :
{json.dumps(template, ensure_ascii=False, indent=2)}

Chaque valeur est un ou plusieurs paragraphes en français, sans Markdown.
N'affiche aucun nom de fichier ni ligne Source/Indice source.
Utilise seulement les preuves. Ne recalcule pas Frascati.

Résumé Frascati :
{json.dumps(frascati_summary or {}, ensure_ascii=False)}

Preuves :
{build_sources_block(sources, max_chars_per_source=650)}
""".strip()
