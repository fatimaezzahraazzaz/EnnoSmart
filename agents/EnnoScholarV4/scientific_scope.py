"""Temporal context, not a relevance score or a replacement search engine."""
from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class ScientificScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str = Field(default="", max_length=2000)
    scientific_question: str = Field(default="", max_length=3000)
    technical_context: str = Field(default="", max_length=4000)
    objectives: list[str] = Field(default_factory=list, max_length=20)
    uncertainties: list[str] = Field(default_factory=list, max_length=20)
    experimental_conditions: list[str] = Field(default_factory=list, max_length=30)
    constraints: list[str] = Field(default_factory=list, max_length=30)
    unconfirmed_assumptions: list[str] = Field(default_factory=list, max_length=20)
    cir_year: int | None = Field(default=None, ge=2000, le=2100)
    start_date: date | None = None
    reference_date: date | None = None
    work_type: Literal["exploration", "plan", "state_of_art", "revision"] = "exploration"


def temporal_scope(state):
    scope = ScientificScope.model_validate(state.get("scientific_scope", {}))
    cutoff = scope.reference_date or scope.start_date
    return {**scope.model_dump(mode="json"), "knowledge_cutoff": cutoff.isoformat() if cutoff else None,
        "period_end": f"{scope.cir_year}-12-31" if scope.cir_year else None,
        "reference_date_confirmed": cutoff is not None,
        "clarification_needed": scope.work_type != "exploration" and cutoff is None,
        "guidance": "Une année CIR seule ne prouve pas la disponibilité au démarrage. Clarifier la date si cette conclusion est nécessaire ; une exploration reste possible."}


def literature_coverage(state, registry):
    scope = temporal_scope(state)
    cutoff = scope["knowledge_cutoff"] or scope["period_end"]
    rows = []
    for article in registry.values():
        records = state.get("availability_records", {}).get(article.id, [])
        # Verified provider metadata is usable without inventing a preprint date.
        if article.verified and article.publication_date:
            try:
                published = date.fromisoformat(article.publication_date[:10])
                records = [*records, {"date": published.isoformat(), "kind": "provider_metadata",
                    "date_type_uncertain": True,
                    "source_url": article.url, "verification_sources": article.verification_sources}]
            except ValueError:
                pass
        dates = sorted(r["date"] for r in records)
        earliest = dates[0] if dates else None
        status = "date_unverified"
        if earliest and cutoff:
            status = "available_by_reference" if earliest <= cutoff else "after_reference"
        elif cutoff and article.verified and article.year and article.year > int(cutoff[:4]):
            status = "after_reference_year"
        rows.append({"article_id": article.id, "title": article.title, "year": article.year,
            "first_verified_availability": earliest, "date_provenance": records, "temporal_status": status,
            "relevance_decision": article.assessment.get("decision", "unexamined")})
    latest = state.get("literature_coverage_review", {})
    return {"scope": scope, "articles": rows, "scientific_assessment": latest,
        "coverage_sufficient": bool(latest.get("sufficient") and cutoff and scope["reference_date_confirmed"]
            and not latest.get("missing_axes") and not latest.get("uncertainties")),
        "guidance": "Disponibilité temporelle et pertinence sont distinctes. Conserver les fondements utiles ; vérifier les travaux récents et les solutions possibles. Aucun nombre imposé, aucune éligibilité CIR déduite."}
