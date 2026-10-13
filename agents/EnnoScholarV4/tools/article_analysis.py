"""Validate analyses authored by the same conversational model against exact passages."""
from ..evidence import validate_claims, exact_source_valid


class ArticleAnalyzer:
    @staticmethod
    def save(article, analysis, evidence, shown, snapshots):
        data = analysis.model_dump()
        for claims in data.values():
            if isinstance(claims, list):
                for claim in claims:
                    if any(eid not in evidence or not exact_source_valid(evidence[eid], snapshots) for eid in claim["evidence_ids"]):
                        raise ValueError("Preuve inconnue ou citation incohérente avec le document conservé.")
        validated = validate_claims(data, article, evidence, shown)
        for name, original in {"scientific_problem": "problem", "research_objective": "objective",
            "proposed_method": "method", "main_results": "results", "contributions": "contribution",
            "relevance_to_current_topic": "relevance"}.items():
            validated[name] = validated[original]
        article.analysis, article.analysis_basis = validated, validated["analysis_basis"]
        # Only an actually saved analysis can advance this state.
        if article.analysis_basis == "fulltext":
            article.access_status = "fulltext_analyzed"
        return validated
