
from __future__ import annotations

from typing import Any

POLICY_VERSION = "ennoamel_visible_candidate_v3_18_1"


def visible_candidate_message(
    warnings: list[str] | None,
) -> str:
    values = [str(value) for value in (warnings or []) if str(value).strip()]
    if not values:
        return (
            "J'ai préparé une nouvelle version sans remplacer la version active. "
            "Consultez le comparatif, puis validez-la ou demandez-moi une correction."
        )

    has_scientific = any(
        value.startswith(
            (
                "citation_non_etayee:",
                "scientific_entailment_blocking",
                "preuves_validees_non_utilisees:",
                "accepted_source_coverage_blocking",
            )
        )
        for value in values
    )
    has_source = any(
        value.startswith(
            (
                "source_fact_missing:",
                "source_fact_altered:",
                "document_block_missing:",
                "document_block_changed:",
                "references_perdues:",
                "renvois_visuels_perdus:",
                "mesures_perdues:",
                "protected_fragment_missing:",
                "protected_fragment_duplicated:",
                "source_integrity_blocking",
            )
        )
        for value in values
    )

    if has_scientific and has_source:
        detail = (
            "Certaines alertes concernent à la fois la conservation du texte source "
            "et la justification scientifique des ajouts."
        )
    elif has_scientific:
        detail = (
            "Certaines citations ou preuves scientifiques demandent encore une "
            "vérification ou une correction."
        )
    elif has_source:
        detail = (
            "Certaines différences avec le texte source demandent encore une "
            "vérification."
        )
    else:
        detail = "Certaines alertes de qualité restent à vérifier."

    return (
        "J'ai préparé une nouvelle version et je la laisse visible pour votre contrôle. "
        + detail
        + " La version active reste inchangée tant que vous ne validez pas cette "
          "proposition. Vous pouvez accepter la version ou me demander de corriger "
          "uniquement les alertes signalées."
    )


def review_summary(warnings: list[str] | None) -> dict[str, Any]:
    values = [str(value) for value in (warnings or []) if str(value).strip()]
    return {
        "policy_version": POLICY_VERSION,
        "candidate_visible": True,
        "active_version_mutated": False,
        "warning_count": len(values),
        "warnings": values,
        "requires_consultant_review": bool(values),
    }
