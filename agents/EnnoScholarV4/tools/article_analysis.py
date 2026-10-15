"""Validate analyses authored by the same conversational model against exact passages."""
import copy
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
        previous = article.analysis or {}
        if previous:
            # A targeted follow-up enriches the same scientific card. Claims are
            # keyed by their exact statement, origin and source basis; evidence
            # from either reading is retained, never silently replaced.
            for name, value in previous.items():
                if isinstance(value, list) and name not in {"evidence", "method_families", "analysis_limits"}:
                    merged = copy.deepcopy(value)
                    for claim in validated.get(name, []):
                        match = next((old for old in merged if
                            (old.get("statement"), old.get("origin"), old.get("basis")) ==
                            (claim.get("statement"), claim.get("origin"), claim.get("basis"))), None)
                        if match is None:
                            merged.append(claim)
                        else:
                            match["evidence_ids"] = list(dict.fromkeys([*match["evidence_ids"], *claim["evidence_ids"]]))
                    validated[name] = merged
                elif name not in validated:
                    validated[name] = value
            proofs = {row["evidence_id"]: row for row in previous.get("evidence", [])}
            proofs.update({row["evidence_id"]: row for row in validated["evidence"]})
            validated["evidence"] = list(proofs.values())
            validated["method_families"] = list(dict.fromkeys([
                *previous.get("method_families", []), previous.get("method_family", ""),
                validated.get("method_family", "")]))
            validated["method_families"] = [value for value in validated["method_families"] if value]
            validated["analysis_limits"] = list(dict.fromkeys([
                *previous.get("analysis_limits", []), previous.get("analysis_limit", ""),
                validated.get("analysis_limit", "")]))
            validated["analysis_limits"] = [value for value in validated["analysis_limits"] if value]
            bases = {row.get("basis") for row in validated["evidence"]}
            validated["analysis_basis"] = "fulltext" if bases == {"fulltext"} else "abstract"
            validated["fulltext_truncated"] = previous.get("fulltext_truncated", False) or validated["fulltext_truncated"]
            for name in ("method_family", "analysis_limit"):
                validated[name] = validated.get(name) or previous.get(name, "")
        # Compatibility aliases must point at the accumulated claims too.
        for name, original in {"scientific_problem": "problem", "research_objective": "objective",
            "proposed_method": "method", "main_results": "results", "contributions": "contribution",
            "relevance_to_current_topic": "relevance"}.items():
            validated[name] = validated[original]
        validated["reading_coverage"] = "targeted_passages"
        article.analysis, article.analysis_basis = validated, validated["analysis_basis"]
        # Only an actually saved analysis can advance this state.
        if any(row.get("basis") == "fulltext" for row in validated["evidence"]):
            article.access_status = "fulltext_analyzed"
        return validated
