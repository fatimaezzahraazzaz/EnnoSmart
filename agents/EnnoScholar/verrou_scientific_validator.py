# -*- coding: utf-8 -*-
from __future__ import annotations

"""
verrou_scientific_validator.py — EnnoScholar V3

Valide scientifiquement un verrou à partir des articles classés.
Ne remplace pas le consultant.
"""

from typing import Any, Dict, List


def validate_verrou_scientifically(intent: Dict[str, Any], articles: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not articles:
        return {
            "scientific_support_score": 0.0,
            "rnd_uncertainty_score": 0.0,
            "engineering_only_risk": 1.0,
            "decision": "aucun_article_trouve",
            "gap_analysis": (
                "Aucun article exploitable n’a été trouvé automatiquement. "
                "Le verrou doit être reformulé ou recherché manuellement."
            ),
            "consultant_action": "Vérifier les mots-clés et lancer une recherche manuelle ciblée.",
        }

    top = articles[:8]
    direct = [a for a in top if a.get("tag") == "Direct"]
    connexe = [a for a in top if a.get("tag") == "Connexe"]

    avg_top5 = sum(float(a.get("relevance_score") or 0) for a in top[:5]) / max(1, min(5, len(top)))

    # V32 : le nombre d'articles Direct/Connexe doit compter plus fortement,
    # sinon tout reste "support faible" même quand la recherche est techniquement bonne.
    support = (
        avg_top5
        + min(len(direct) * 0.12, 0.30)
        + min(len(connexe) * 0.055, 0.22)
    )
    support = min(support, 1.0)

    rnd_uncertainty = min(1.0, support * 0.82 + (0.12 if direct else 0.04 if connexe else 0.0))
    engineering_risk = max(0.0, 1.0 - support)

    if direct and support >= 0.55:
        decision = "verrou_scientifiquement_defendable"
        action = "Conserver le verrou et sélectionner les articles Direct/Connexe pertinents pour l’état de l’art."
    elif (direct or connexe) and support >= 0.28:
        decision = "verrou_a_confirmer_par_etat_art"
        action = "Garder le verrou en validation consultant et compléter la recherche si nécessaire."
    else:
        decision = "support_scientifique_faible"
        action = "Reformuler le verrou ou vérifier s’il relève plutôt d’une activité d’ingénierie courante."

    gap = (
        f"La recherche automatique a trouvé {len(direct)} article(s) Direct et "
        f"{len(connexe)} article(s) Connexe. "
        "Ces sources situent le problème dans la littérature, mais la validation finale doit vérifier "
        "si les solutions existantes couvrent ou non le cas spécifique du projet."
    )

    return {
        "scientific_support_score": round(support, 4),
        "rnd_uncertainty_score": round(rnd_uncertainty, 4),
        "engineering_only_risk": round(engineering_risk, 4),
        "decision": decision,
        "gap_analysis": gap,
        "consultant_action": action,
    }
