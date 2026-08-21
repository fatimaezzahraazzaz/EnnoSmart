# -*- coding: utf-8 -*-
from __future__ import annotations

"""
verrou_scientific_validator.py — EnnoScholar V132

Validation prudente et consciente des erreurs API :
- le score scientifique est calculé uniquement avec les articles académiques ;
- les sources techniques reconnues restent séparées ;
- si les API ont échoué en 429 et qu'aucun article n'est trouvé, on retourne
  recherche_incomplete_api_limited au lieu de aucun_article_trouve.
"""

from typing import Any, Dict, List


def _is_technical_source(a: Dict[str, Any]) -> bool:
    return a.get("source") == "technical_catalog" or a.get("source_type") == "technical_reference" or a.get("tag") == "Technique"


def _api_limited(errors: List[Dict[str, Any]] | None) -> bool:
    errors = errors or []
    if not errors:
        return False
    limited = 0
    for e in errors:
        msg = str(e.get("error") or "")
        if e.get("api_limited") or e.get("http_status") == 429 or "429" in msg or "Too Many Requests" in msg:
            limited += 1
    return limited > 0 and limited >= max(1, len(errors) // 2)


def _all_attempts_failed(errors: List[Dict[str, Any]] | None, articles: List[Dict[str, Any]]) -> bool:
    if articles:
        return False
    return bool(errors)


def validate_verrou_scientifically(
    intent: Dict[str, Any],
    articles: List[Dict[str, Any]],
    technical_sources: List[Dict[str, Any]] | None = None,
    errors: List[Dict[str, Any]] | None = None,
    search_status: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    technical_sources = technical_sources or []
    errors = errors or []
    search_status = search_status or {}

    # V167: a planning failure is not a literature result.
    if bool(search_status.get("query_planning_failed")):
        workflow = search_status.get("query_workflow") if isinstance(search_status.get("query_workflow"), dict) else {}
        return {
            "scientific_support_score": 0.0,
            "technical_reference_support_score": 0.0,
            "rnd_uncertainty_score": 0.0,
            "engineering_only_risk": 0.0,
            "decision": "recherche_non_lancee_planification_requetes",
            "search_incomplete": True,
            "search_executed": False,
            "query_planning_failed": True,
            "api_limited": False,
            "gap_analysis": (
                "La recherche scientifique n’a pas été lancée : EnnoScholar n’a pas pu construire "
                "des requêtes suffisamment fiables après validation et réparation automatiques. "
                "Ce statut ne signifie pas qu’aucun article scientifique n’existe."
            ),
            "consultant_action": (
                "Aucune conclusion scientifique n’est proposée. Vérifier les preuves du verrou ou relancer "
                "la planification des requêtes ; les bases scientifiques n’ont pas été interrogées."
            ),
            "query_workflow": workflow,
            "consultant_status_label": "Recherche non lancée — requêtes non validées",
        }

    academic = [a for a in (articles or []) if isinstance(a, dict) and not _is_technical_source(a)]
    technical_count = len(technical_sources) + len([a for a in (articles or []) if isinstance(a, dict) and _is_technical_source(a)])
    api_limited = bool(search_status.get("api_limited") or _api_limited(errors))
    all_failed = bool(search_status.get("all_sources_failed") or _all_attempts_failed(errors, academic))

    if not academic:
        if api_limited and all_failed:
            return {
                "scientific_support_score": 0.0,
                "technical_reference_support_score": round(min(technical_count * 0.08, 0.24), 4),
                "rnd_uncertainty_score": 0.0,
                "engineering_only_risk": 0.0,
                "decision": "recherche_incomplete_api_limited",
                "search_incomplete": True,
                "api_limited": True,
                "gap_analysis": (
                    "Aucun article académique exploitable n’a été récupéré, mais la recherche est incomplète "
                    "car les API scientifiques ont renvoyé des erreurs de limitation 429 / Too Many Requests. "
                    f"{technical_count} source(s) technique(s) reconnue(s) peuvent orienter la recherche, "
                    "mais il faut relancer après temporisation ou utiliser le cache avant de conclure."
                ),
                "consultant_action": "Ne pas rejeter le verrou. Relancer la recherche avec cache/retry ou effectuer une recherche manuelle ciblée.",
            }

        return {
            "scientific_support_score": 0.0,
            "technical_reference_support_score": round(min(technical_count * 0.08, 0.24), 4),
            "rnd_uncertainty_score": 0.0,
            "engineering_only_risk": 1.0,
            "decision": "aucun_article_trouve",
            "search_incomplete": False,
            "api_limited": api_limited,
            "gap_analysis": (
                f"Aucun article académique exploitable n’a été trouvé automatiquement. "
                f"{technical_count} source(s) technique(s) reconnue(s) peuvent aider à orienter la recherche, "
                "mais elles ne suffisent pas à valider l’état de l’art."
            ),
            "consultant_action": "Relancer avec des mots-clés ciblés ou effectuer une recherche manuelle. Les sources techniques doivent être vérifiées séparément.",
        }

    top = academic[:15]
    direct = [a for a in top if a.get("tag") == "Direct"]
    connexe = [a for a in top if a.get("tag") == "Connexe"]
    fondamental = [a for a in top if a.get("tag") == "Fondamental"]

    avg_top5 = sum(float(a.get("relevance_score") or 0) for a in top[:5]) / max(1, min(5, len(top)))
    avg_top10 = sum(float(a.get("relevance_score") or 0) for a in top[:10]) / max(1, min(10, len(top)))

    # V132 : score plus prudent ; on récompense la quantité jusqu'à un plafond,
    # mais pas au point de déclarer automatiquement 100%.
    support = (
        0.68 * avg_top5
        + 0.22 * avg_top10
        + min(len(direct) * 0.045, 0.22)
        + min(len(connexe) * 0.025, 0.12)
    )
    support = max(0.0, min(support, 0.88))

    technical_reference_support = min(technical_count * 0.08, 0.24)
    rnd_uncertainty = min(1.0, support * 0.82 + (0.07 if direct else 0.03 if connexe else 0.0))
    engineering_risk = max(0.06, 1.0 - support)

    if len(direct) >= 3 and support >= 0.64:
        decision = "verrou_scientifiquement_defendable"
        action = "Conserver le verrou, puis sélectionner manuellement les articles Direct vraiment exploitables avant rédaction."
    elif len(direct) >= 1 and support >= 0.40:
        decision = "verrou_a_confirmer_par_etat_art"
        action = "Garder le verrou en validation consultant et compléter/filtrer la sélection d’articles."
    elif len(connexe) >= 2 and support >= 0.32:
        decision = "support_connexe_a_completer"
        action = "Le verrou est plausible, mais il faut renforcer la recherche avec des articles plus directs."
    else:
        decision = "support_scientifique_faible"
        action = "Ne pas conclure. Reformuler le verrou ou effectuer une recherche manuelle ciblée."

    if api_limited:
        # Même avec des articles, informer que le résultat peut être incomplet.
        action += " Attention : certaines API ont été limitées, le résultat peut être incomplet."

    gap = (
        f"La recherche automatique a trouvé {len(direct)} article(s) Direct, "
        f"{len(connexe)} article(s) Connexe, {len(fondamental)} article(s) Fondamental "
        f"et {technical_count} source(s) technique(s) reconnue(s). "
        "Le score scientifique est calculé uniquement sur les articles académiques ; "
        "les sources techniques servent de compléments à vérifier."
    )
    if api_limited:
        gap += " Certaines requêtes ont été limitées par les API ; le rapport doit être considéré comme partiel."

    return {
        "scientific_support_score": round(support, 4),
        "technical_reference_support_score": round(technical_reference_support, 4),
        "rnd_uncertainty_score": round(rnd_uncertainty, 4),
        "engineering_only_risk": round(engineering_risk, 4),
        "decision": decision,
        "search_incomplete": bool(api_limited),
        "api_limited": bool(api_limited),
        "gap_analysis": gap,
        "consultant_action": action,
    }

# ENNOSCHOLAR_V167_LANGGRAPH_QUERY_WORKFLOW
