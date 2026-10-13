# -*- coding: utf-8 -*-
from __future__ import annotations

"""Sélecteur strict des verrous transmis à EnnoScholar.

EnnoScholar ne transforme plus les limites, méthodes, paramètres ou résultats
NLP en nouveaux verrous. La qualification appartient à EnnoDiagnostic et la
liste reçue ici doit déjà avoir été confirmée par le consultant.
"""

from typing import Any, Dict

from .contracts import ContractError, build_confirmed_contract


def select_confirmed_verrous(
    payload: Dict[str, Any],
    *,
    aliases: Any = None,
    source_path: str = "",
) -> Dict[str, Any]:
    contract = build_confirmed_contract(
        payload,
        aliases=aliases,
        source_path=source_path,
    )
    return {
        "domain_detection": payload.get("domain_detection") or {},
        "verrous": contract["verrous"],
        "verrous_count": contract["verrous_count"],
        "verrou_fingerprint": contract["verrou_fingerprint"],
        "selector": {
            "version": "confirmed_verrous_only_v1",
            "source": "EnnoDiagnostic + validation consultant",
            "reconstructs_verrous_from_nlp": False,
            "creates_identifiers": False,
        },
        "confirmed_contract": contract,
    }


def select_scholar_verrous_from_nlp(
    nlp_result: Dict[str, Any],
    max_verrous: int = 0,
) -> Dict[str, Any]:
    """Alias historique, désormais fail-closed.

    ``max_verrous`` est conservé pour compatibilité d'appel, mais aucune
    troncature n'est autorisée : elle modifierait la liste confirmée.
    """

    result = select_confirmed_verrous(nlp_result)
    count = len(result["verrous"])
    if max_verrous and max_verrous < count:
        raise ContractError(
            "confirmed_verrous_truncation_forbidden",
            "Il est interdit de tronquer la liste des verrous confirmés.",
            {"confirmed_count": count, "requested_max": max_verrous},
        )
    return result

