"""Domain-independent exploration policy over the existing corpus and tools.

Scientific judgments remain model decisions backed by exact sources. This module
counts saved artifacts, exposes unfinished work and bounds revisits; it does not
rank papers by keywords or invent a scientific taxonomy.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class DimensionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: Literal["methods", "experimental_protocol", "results", "limits", "comparisons", "theory"]
    status: Literal["verified", "absent", "unavailable", "unread", "ambiguous", "not_applicable"]
    scope: Literal["examined_passages", "explicit_statement", "available_document"] = "examined_passages"
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    reason: str = Field(min_length=1, max_length=2000)


class ArticleDepthReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    article_id: str
    article_type: Literal["experimental", "theoretical", "mixed", "unknown"]
    sufficient: bool
    dimensions: list[DimensionReview] = Field(min_length=1, max_length=6)


class ResearchAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["read", "triage", "rerank", "inspect", "search", "similar", "web"]
    article_ids: list[str] = Field(default_factory=list, max_length=20)
    query: str = Field(default="", max_length=1500)
    family: str = Field(default="", max_length=160)
    sources: list[Literal["openalex", "semantic_scholar", "arxiv"]] = Field(default_factory=list, max_length=3)
    offset: int = Field(default=0, ge=0)
    limit_per_source: int = Field(default=12, ge=1, le=100)
    mode: Literal["similar", "alternatives", "references", "citations"] = "similar"
    reason: str = Field(min_length=1, max_length=2000)


class ResearchReviewArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sufficient: bool = False
    exhausted: bool = False
    families: list[str] = Field(default_factory=list, max_length=30)
    gaps: list[str] = Field(default_factory=list, max_length=30)
    next_queries: list[str] = Field(default_factory=list, max_length=12)
    reason: str = Field(min_length=1, max_length=4000)
    analysis_sufficient: bool = False
    scientific_selection_justified: bool = False
    article_reviews: list[ArticleDepthReview] = Field(default_factory=list, max_length=20)
    actions: list[ResearchAction] = Field(default_factory=list, max_length=12)
    stop_reason: str | None = Field(default=None, max_length=1000)


BIBLIOGRAPHIC_OUTPUT = re.compile(r"^\W*(?:(?:des|les|of|the|some|cinq|five|\d+)\s+)*(?:articles?|publications?|papers?|"
    r"travaux|études|etudes|studies|références?|references?|revues?|reviews?|state[- ]of[- ]the[- ]art|état\s+de\s+l)\b", re.I)


class NeedCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^c\d{1,2}$", description="c1, c2…")
    statement: str = Field(min_length=3, max_length=300, description="Condition vérifiable sur une publication, tirée du "
        "besoin (ex. : ce que la méthode produit réellement, son objet, sa dimension ou son échelle, le rôle de la méthode demandée).")
    essential: bool = Field(description="true : une publication qui ne la remplit pas ne répond pas directement au besoin.")


class ApproachFamily(BaseModel):
    model_config = ConfigDict(extra="forbid")
    family: str = Field(min_length=2, max_length=160, description="Famille d'approches scientifiques plausible pour ce besoin.")
    query: str = Field(min_length=3, max_length=300, description="Requête bibliographique en anglais propre à cette famille.")
    why: str = Field(max_length=300)


class NeedProfile(BaseModel):
    """The scientific need of a literature search, declared once by the model with its goal.
    Generic in form; its content comes from the consultant's request, in any field."""
    model_config = ConfigDict(extra="forbid")
    object: str = Field(max_length=400, description="Objet, système ou phénomène étudié.")
    problem: str = Field(max_length=500, description="Problème scientifique ou technologique à résoudre.")
    expected_output: str = Field(max_length=400, description="Sortie technique produite par les méthodes recherchées (l'objet, "
        "le résultat ou la propriété qu'une méthode directe produit), jamais le livrable bibliographique du consultant "
        "(« des articles décrivant… »).")
    acceptable_methods: list[str] = Field(max_length=8, description="Méthodes acceptables ; [] si aucune restriction.")
    application_context: str = Field(max_length=400)
    constraints: list[str] = Field(max_length=8)
    criteria: list[NeedCriterion] = Field(min_length=1, max_length=8,
        description="Critères de pertinence ; les essentiels distinguent une contribution directe d'une contribution connexe.")
    direct: str = Field(max_length=300, description="Ce qu'est une contribution directe pour ce besoin.")
    related: str = Field(max_length=300, description="Ce qu'est une contribution connexe (problème voisin, méthode transposable).")
    foundational: str = Field(max_length=300, description="Ce qu'est une contribution fondamentale (méthode ou résultat de référence).")
    families: list[ApproachFamily] = Field(min_length=1, max_length=8,
        description="Carte initiale des familles d'approches, chacune avec sa requête ; complémentaires, sans doublon.")
    uncertainties: list[str] = Field(max_length=6, description="Points à clarifier pendant la recherche.")

    @model_validator(mode="after")
    def distinct(self):
        if BIBLIOGRAPHIC_OUTPUT.match(self.expected_output):
            # Run 111cf83c: « Articles scientifiques décrivant des méthodes… » framed the search, not the science.
            raise ValueError("expected_output décrit la sortie technique produite par les méthodes recherchées, "
                "pas le livrable bibliographique demandé (articles, publications…).")
        ids = [c.id for c in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("Critères du besoin : identifiants c1, c2… uniques.")
        if not any(c.essential for c in self.criteria):
            raise ValueError("Besoin : au moins un critère essentiel distingue une contribution directe.")
        queries = [" ".join(f.query.casefold().split()) for f in self.families]
        if len(queries) != len(set(queries)):
            raise ValueError("Familles d'approches : une requête distincte par famille.")
        return self


class CriterionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion_id: str = Field(pattern=r"^c\d{1,2}$")
    status: Literal["met", "partial", "not_met", "unknown"] = Field(description="met seulement si la source établit chaque "
        "élément du critère (nature, dimension, échelle…) ; unknown si elle ne permet pas de l'établir.")
    basis: Literal["abstract", "title", "saved_claims", "fulltext", "inference"] = Field(
        description="Source du jugement ; inference = déduction non énoncée par la source (jamais suffisante pour met).")
    note: str = Field(max_length=300)
    evidence: str = Field(default="", max_length=300, description="met : courte citation exacte de la source (abstract, "
        "titre ou fiche) qui établit le critère ; \"\" sinon.")


class RelevanceProfile(BaseModel):
    """A model judgment for this need, not a new scientific claim or a fixed taxonomy."""
    model_config = ConfigDict(extra="forbid")
    problem: str = Field(max_length=500)
    output: str = Field(max_length=500)
    method: str = Field(max_length=500)
    object_scope: str = Field(max_length=400, description="Nature, dimension, échelle ou population pertinente ; inconnue si non établie.")
    ai_role: str = Field(max_length=500)
    conditions: str = Field(max_length=500)
    results_validation: str = Field(max_length=800)
    fit: str = Field(max_length=600)
    uncertainties: list[str] = Field(max_length=8)
    criteria: list[CriterionCheck] = Field(default_factory=list, max_length=8,
        description="Un jugement par critère de research_need (statut, source, note) ; [] sans besoin structuré.")
    missing_information: list[str] = Field(default_factory=list, max_length=6,
        description="Informations absentes de la notice qui changeraient le jugement (protocole, métriques, dimension…).")


class RankingReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incoming_id: str | None = Field(default=None, description="Concurrent entrant ; null pour un retrait motivé sans remplissage.")
    outgoing_id: str
    justification: str = Field(min_length=20, max_length=1800)
    information_used: list[str] = Field(min_length=1, max_length=8,
        description="Informations scientifiques comparées dans les dossiers transmis ; pas de supériorité numérique sans protocole comparable.")
    uncertainties: list[str] = Field(max_length=8)


class DiscoveryFamily(BaseModel):
    model_config = ConfigDict(extra="forbid")
    family: str = Field(min_length=1, max_length=200)
    status: Literal["covered", "searched", "missing", "unavailable", "not_relevant"]
    reason: str = Field(min_length=1, max_length=600)
    article_ids: list[str] = Field(default_factory=list, max_length=12)


class DiscoveryAction(ResearchAction):
    kind: Literal["triage", "rerank", "inspect", "search", "similar", "web"]


class DiscoveryReviewArgs(BaseModel):
    """Small discovery checkpoint; never certifies reading depth or final completeness."""
    model_config = ConfigDict(extra="forbid")
    ready_for_reading: bool
    families: list[DiscoveryFamily] = Field(min_length=1, max_length=12)
    gaps: list[str] = Field(default_factory=list, max_length=12)
    reason: str = Field(min_length=1, max_length=1800)
    actions: list[DiscoveryAction] = Field(default_factory=list, max_length=3)
    stop_reason: str | None = Field(default=None, max_length=600)

def requested_count(message):
    match = re.search(r"\b(\d{1,3})\s+(?:(?:meilleurs?|best|scientific|scientifiques?|most\s+relevant)\s+)*"
        r"(?:articles?|publications?|papers?|references?|références?|travaux)\b", message or "", re.I)
    return max(1, min(200, int(match[1]))) if match else None


def wants_research(message):
    text = message or ""
    if re.search(r"aucune\s+(?:nouvelle\s+)?recherche|sans\s+(?:nouvelle\s+)?recherche|"
            r"n['’]effectue\s+(?:aucune|pas)|no\s+(?:new\s+)?search", text, re.I):
        return False
    return bool(re.search(r"\b(?:recherch\w*|cherch\w*|trouv\w*|search|find|identify)\b", text, re.I)
        and re.search(r"articles?|publications?|papers?|littérature|literature|travaux", text, re.I))


def active(state):
    goal = state.get("turn_goal") or {}
    return goal.get("deliverable") == "literature_search" and goal.get("search_needed") is not False \
        and "no_search" not in (goal.get("constraints") or [])


def begin(state, message, message_id=None):
    progress = state.get("research_progress")
    if not progress or progress.get("request_message_id") != message_id:
        progress = {"id": "research_" + hashlib.sha256(str(message_id or message).encode()).hexdigest()[:24],
            "request_message_id": message_id, "requested_count": requested_count(message), "reviews": [],
            "discovery_rounds": 0}
        state["research_progress"] = progress
    count = (state.get("turn_goal") or {}).get("requested_article_count")
    if count is not None and progress.get("requested_count") is None:
        progress["requested_count"] = count
    return progress


def signature(state):
    """What the corpus knows: decisions, cards, searches, web sources, readings. The
    bibliographic verification flag is left out: the final check of a publication must
    not make a coverage review written just before it look outdated."""
    articles = state.get("articles", {})
    value = {"articles": {aid: {k: a.get(k) for k in ("assessment", "analysis", "selection_override")}
        for aid, a in articles.items()}, "searches": state.get("recent_searches", []),
        "ranking": state.get("evaluation", {}).get("recommended_article_ids", []),
        "web_sources": state.get("web_sources", []),
        "reading": [{k: t.get(k) for k in ("article_id", "source_fingerprint", "examined_evidence_ids",
            "reviewed_without_result_evidence_ids", "access_attempted")}
            for t in state.get("scientific_reading", {}).get("tasks", [])]}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


# Dimensions of a card a scientific answer is built from (methods, protocol, results, limits).
CARD_GROUPS = {"method": ("method", "technical_mechanism", "equations"),
    "protocol": ("experimental_setup", "validation_protocol", "datasets_materials", "parameters"),
    "results": ("results", "metrics", "tables_figures"),
    "limitations": ("limitations", "conditions_of_validity", "generalization")}
LEVEL_RANK = {"documented": 0, "fulltext_partial": 1, "abstract_only": 2, "pending": 3}


def version_groups(state):
    """Verified bibliographic versions of one work (same record id, or an identity verified by
    authors, title, period and registry metadata). Only selection, counting and display group
    them: each version keeps its own card and proofs, never merged across texts."""
    rows = state.get("articles", {})
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        if a in rows and b in rows:
            parent[find(a)] = find(b)
    for aid, a in rows.items():
        canonical = a.get("canonical_work_id")
        if canonical and canonical != aid:
            union(aid, canonical)
    for aid, links in (state.get("version_correspondences") or {}).items():
        for link in links or []:
            if str(link.get("relation", "")).startswith("verified"):
                union(aid, link.get("article_id"))
    return {aid: find(aid) for aid in rows}


def card_level(state, aid):
    """What a stored card actually documents: « documented » (targeted passages of the full
    text giving the method and results or limits), « fulltext_partial », « abstract_only »,
    or « pending » (no valid card). A card is never upgraded by the panel's recommendation."""
    from .evidence import exact_source_valid
    a = state.get("articles", {}).get(aid) or {}
    analysis = a.get("analysis") or {}
    proofs = [e for e in analysis.get("evidence") or [] if isinstance(e, dict)]
    if not (a.get("verified") and not a.get("metadata_errors") and analysis.get("method") and proofs):
        return "pending", []
    snapshots = state.get("source_snapshots", {})
    if any(e.get("article_id") != aid or not exact_source_valid(e, snapshots) for e in proofs):
        return "pending", []
    body = {e["evidence_id"] for e in proofs if e.get("basis") == "fulltext"}
    groups = [name for name, fields in CARD_GROUPS.items() if any(isinstance(c, dict) and c.get("evidence_ids")
        and set(c["evidence_ids"]) <= body for f in fields for c in analysis.get(f) or [])]
    if not body:
        return "abstract_only", groups
    return ("documented" if "method" in groups and ({"results", "limitations"} & set(groups)) else "fulltext_partial"), groups


def canonical_selection(state):
    """The one selection a scientific answer is built on: the model's recommended works,
    their verified versions grouped (the best-documented version represents the work), the
    analysed ones first in recommendation order, cut at the requested number. The counter,
    the final context, the depth check and the published list all read this selection."""
    rows = state.get("articles", {})
    groups = version_groups(state)
    recommended = [aid for aid in state.get("evaluation", {}).get("recommended_article_ids", [])
        if aid in rows and rows[aid].get("selection_override") is not False]
    members = {}
    for aid, root in groups.items():
        members.setdefault(root, []).append(aid)
    works, seen = [], set()
    for position, aid in enumerate(recommended):
        root = groups.get(aid, aid)
        if root in seen:
            continue
        seen.add(root)
        versions = [x for x in members.get(root, [aid]) if rows[x].get("selection_override") is not False]
        levels = {x: card_level(state, x) for x in versions}
        order = {x: i for i, x in enumerate(recommended)}
        best = min(versions, key=lambda x: (LEVEL_RANK[levels[x][0]], x not in order, order.get(x, 0)))
        works.append({"work_id": root, "article_id": best, "version_ids": versions, "level": levels[best][0],
            "documented_groups": levels[best][1], "position": position})
    target = (state.get("research_progress") or {}).get("requested_count")
    # Availability chooses a VERSION of the same verified work, never the rank
    # between distinct scientific works. A newly preferred unread work keeps its place.
    chosen = works[:target] if target else works
    blocking = [w for w in chosen if w["level"] == "pending"]
    return {"requested_count": target, "selected_ids": [w["article_id"] for w in chosen if w["level"] != "pending"],
        "ranked_ids": [w["article_id"] for w in chosen],
        "pending_ids": [w["article_id"] for w in blocking],
        "available_ids": [w["article_id"] for w in works[len(chosen):]],
        "works": works, "versions": {w["article_id"]: w["version_ids"] for w in works if len(w["version_ids"]) > 1},
        "levels": {w["article_id"]: w["level"] for w in works}}


def status(state):
    rows = state.get("articles", {})
    selection = canonical_selection(state)
    ready, pending = selection["selected_ids"], selection["pending_ids"]
    p = state.get("research_progress") or {}
    review = (p.get("reviews") or [{}])[-1]
    discovery = p.get("discovery_review", {})
    discovery_stop = bool(discovery_current(state) and not discovery.get("ready_for_reading") and discovery.get("stop_reason") and not pending_actions(state))
    current = review.get("signature") == signature(state)
    target = p.get("requested_count")
    # Quantity, semantic depth and justified selection are independent dimensions.
    gaps = review.get("gaps", state.get("evaluation", {}).get("remaining_axes", []))
    target_reached = bool(not pending and ready and (target is None or len(ready) >= target))
    depth = depth_status(state, selection["ranked_ids"])
    analysis_sufficient = bool(current and review.get("analysis_sufficient") and
        ((state.get("turn_goal") or {}).get("reading_depth") != "deep" or depth["sufficient"]))
    justified = bool(current and review.get("scientific_selection_justified") and comparison_current(state) and ranking_current(state))
    complete = bool(target_reached and analysis_sufficient and justified and current and review.get("sufficient")
        and not gaps and not pending_actions(state))
    unexamined = [aid for aid, a in rows.items() if not a.get("assessment") and not a.get("analysis")]
    levels = selection["levels"]
    return {"requested_count": target, "verified_analyzed_ids": ready, "verified_analyzed_count": len(ready),
        # Depth of each selected card: targeted full-text passages or the abstract alone.
        "fulltext_documented_ids": [aid for aid in ready if levels[aid] == "documented"],
        "fulltext_partial_ids": [aid for aid in ready if levels[aid] == "fulltext_partial"],
        "abstract_only_ids": [aid for aid in ready if levels[aid] == "abstract_only"],
        "available_article_ids": selection["available_ids"], "grouped_versions": selection["versions"],
        "pending_article_ids": pending, "candidates_total": len(rows),
        # Bounded in the context: the count is exact, the identifiers are the latest collected.
        "unexamined_ids": unexamined[-20:], "unexamined_count": len(unexamined),
        "gaps": gaps,
        "target_reached": target_reached, "analysis_sufficient": analysis_sufficient,
        "scientific_selection_justified": justified, "depth_review": depth,
        "pending_actions": pending_actions(state), "comparison_current": comparison_current(state),
        "review_current": current, "complete": complete,
        "scientific_coverage_sufficient": bool(complete and current and review.get("sufficient") and not gaps),
        "exhausted": bool(current and review.get("exhausted")), "review": review,
        "partial_stop": discovery_stop or bool(current and not complete and review.get("stop_reason") and not pending_actions(state)),
        "discovery_review": discovery,
        "stop_reason": state.get("scientific_reading", {}).get("blocked_reason"),
        "guidance": "Une limite technique n'est pas une conclusion de rareté scientifique. Les articles en attente "
            "ne sont pas rejetés. Les méthodes, résultats et limites non documentés restent explicitement inconnus."}


def card_signature(state, ids=None):
    articles = state.get("articles", {})
    return hashlib.sha256(json.dumps({aid: {"analysis": {k: v for k, v in (articles[aid].get("analysis") or {}).items()
            if k != "reading_coverage"}, "abstract": articles[aid].get("abstract"),
            "source": {k: (articles[aid].get("fulltext") or {}).get(k) for k in ("text", "chunks", "tables")}}
        for aid in sorted(ids if ids is not None else articles) if aid in articles},
        sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def depth_status(state, ids):
    reviews = state.get("research_progress", {}).get("article_reviews", {})
    missing = [aid for aid in ids if not reviews.get(aid, {}).get("sufficient") or
        reviews[aid].get("card_signature") != card_signature(state, [aid])]
    return {"sufficient": bool(ids) and not missing, "missing_article_ids": missing,
        "articles": {aid: reviews[aid] for aid in ids if aid in reviews}}


def comparison_current(state, ids=None):
    ids = canonical_selection(state)["selected_ids"] if ids is None else ids
    if len(ids) < 2:
        return bool(ids)
    covered = set()
    for receipt in state.get("research_comparisons", []):
        compared = receipt["article_ids"]
        if receipt.get("card_signature") == card_signature(state, compared):
            covered.update(set(compared) & set(ids))
    return set(ids) <= covered


def pending_actions(state):
    return sorted([a for a in state.get("research_progress", {}).get("actions", []) if a.get("status") == "pending"],
        key=lambda a: not a.get("local", False))


def ranking_current(state):
    return not stale_ranking_candidates(state)


def need_signature(state):
    goal = state.get("turn_goal") or {}
    value = [state.get("scientific_scope"),
        {k: state.get("research_state", {}).get(k) for k in ("topic", "scientific_question", "objectives", "exclusions")},
        {k: goal.get(k) for k in ("understanding", "article_scope", "article_ids", "constraints")}]
    if state.get("research_need"):
        # The structured need (criteria) is part of what every relevance judgment depends on;
        # absent in sessions predating it, so their receipts stay valid.
        value.append({k: v for k, v in state["research_need"].items() if k not in {"families", "uncertainties"}})
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def candidate_signature(state, aid):
    article = state.get("articles", {}).get(aid, {})
    # The ranking dossier contains notices and saved science, not unread PDF bytes.
    # Merely downloading a body must not charge another relevance judgment.
    groups = version_groups(state)
    versions = [other for other in sorted(groups) if groups[other] == groups.get(aid) and
        state["articles"][other].get("selection_override") is not False and other not in state.get("held_article_ids", [])]
    science = {other: {"abstract": state["articles"][other].get("abstract"),
        "basis": state["articles"][other].get("analysis_basis"), "analysis": {k: v for k, v in
            (state["articles"][other].get("analysis") or {}).items() if k != "reading_coverage"},
        "metadata": {k: state["articles"][other].get(k) for k in ("title", "doi", "arxiv_id", "year")}}
        for other in versions}
    return hashlib.sha256(json.dumps([science,
        article.get("selection_override"),
        aid in state.get("held_article_ids", [])], sort_keys=True, default=str).encode()).hexdigest()


def stale_ranking_candidates(state):
    progress = state.get("research_progress", {})
    records = progress.get("ranking_records")
    candidates = ranking_candidates(state)
    if records is None:
        # Read-only compatibility for checkpoints predating per-candidate receipts.
        old = progress.get("ranking_review", {})
        covered = set(old.get("reviewed_ids", [])) if old.get("card_signature") == card_signature(state) else set()
        return [aid for aid in candidates if aid not in covered]
    need = need_signature(state)
    return [aid for aid in candidates if records.get(aid, {}).get("need_signature") != need or
        any(candidate_signature(state, dep) != version for dep, version in records.get(aid, {}).get("dependencies", {}).items()) or
        aid not in records.get(aid, {}).get("dependencies", {})]


def ranking_candidates(state):
    return [aid for aid, a in sorted(state.get("articles", {}).items()) if a.get("selection_override") is not False
        and aid not in state.get("held_article_ids", []) and (a.get("assessment") or {}).get("decision") != "reject"]


def record_ranking(state, decisions):
    """A reading changes its own decisions and comparisons, never the whole corpus."""
    progress = state.setdefault("research_progress", {})
    if "ranking_records" not in progress:
        old = progress.get("ranking_review", {})
        progress["ranking_records"] = {aid: {"dependencies": {aid: candidate_signature(state, aid)},
            "need_signature": need_signature(state)} for aid in old.get("reviewed_ids", [])
            if aid in state.get("articles", {}) and old.get("card_signature") == card_signature(state)}
    records = progress["ranking_records"]
    for decision in decisions:
        aid = decision["article_id"]
        if aid in state.get("articles", {}):
            records[aid] = {"dependencies": {aid: candidate_signature(state, aid)},
                "need_signature": need_signature(state), "decision": decision}
    reviewed = [aid for aid in records if records[aid].get("need_signature") == need_signature(state) and
        all(candidate_signature(state, dep) == version for dep, version in records[aid]["dependencies"].items())]
    row = {"article_ids": list(state.get("evaluation", {}).get("recommended_article_ids", [])),
        "decisions": decisions, "reviewed_ids": reviewed,
        "card_signature": card_signature(state), "corpus_ids": sorted(state.get("articles", {}))}
    progress.setdefault("ranking_history", []).append(row)
    progress["ranking_review"] = row
    return row


def cumulative_ranking(state, proposed, replacements, verdicts=None):
    """Apply explicit, current comparisons; omission never removes an incumbent.

    Called after ordinary identity/exposure/consultant checks. Invalid replacements
    are reported, not silently applied. All information receipts refer to inputs of
    the completed selection request, not a title or a previous model call.
    """
    rows = state.get("articles", {})
    held = set(state.get("held_article_ids", []))
    eligible = lambda aid: aid in rows and rows[aid].get("selection_override") is not False and aid not in held
    accepted = [aid for aid in rows if eligible(aid) and rows[aid].get("selection_override") is True]
    groups = version_groups(state)
    def unique_works(ids):
        seen, result = set(), []
        for aid in ids:
            if groups.get(aid, aid) not in seen:
                result.append(aid)
                seen.add(groups.get(aid, aid))
        return result
    accepted = unique_works(accepted)
    target = state.get("research_progress", {}).get("requested_count")
    target = max(target or 20, len(accepted))
    previous = [aid for aid in state.get("evaluation", {}).get("recommended_article_ids", []) if eligible(aid)]
    current = unique_works([*accepted, *previous])[:target]
    receipts = state.get("selection_dossier_receipts", {})
    ignored, events, touched = [], [], set()
    for replacement in replacements:
        data = replacement.model_dump() if hasattr(replacement, "model_dump") else replacement
        out, inc = data["outgoing_id"], data.get("incoming_id")
        ids = [out, *([inc] if inc else [])]
        reason = ("not_current_finalist" if out not in current else "consultant_pinned" if out in accepted else
            "duplicate_comparison" if set(ids) & touched else
            "incoming_not_retained" if inc and (inc not in proposed or not eligible(inc) or
                (verdicts or {}).get(inc, (rows[inc].get("assessment") or {}).get("decision")) != "retain") else
            "incoming_already_finalist" if inc and any(groups.get(inc, inc) == groups.get(aid, aid) for aid in current) else
            "comparison_dossier_not_received" if any(receipts.get(aid, {}).get("version") != candidate_signature(state, aid) or
                receipts.get(aid, {}).get("need_signature") != need_signature(state)
                for aid in ids) else None)
        if reason:
            ignored.append({**data, "reason": reason})
            continue
        position = current.index(out)
        current.remove(out)
        if inc:
            current.insert(position, inc)
        touched.update(ids)
        event = {**data, "dependencies": {aid: candidate_signature(state, aid) for aid in ids},
            "need_signature": need_signature(state), "provenance": {aid: receipts[aid] for aid in ids}}
        event["id"] = hashlib.sha256(json.dumps(event, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        history = state.setdefault("research_progress", {}).setdefault("ranking_changes", [])
        if not any(e["id"] == event["id"] for e in history):
            history.append(event)
        events.append(event)
    withdrawn = {event["outgoing_id"] for event in events}
    for aid in proposed:
        if aid in withdrawn:
            continue
        if any(groups.get(aid, aid) == groups.get(other, other) for other in current):
            continue
        if eligible(aid) and aid not in current and len(current) < target:
            current.append(aid)
        elif eligible(aid) and aid not in current:
            ignored.append({"incoming_id": aid, "reason": "explicit_comparison_required"})
    # An explicit order changes order only, not membership or a consultant's priority.
    order = state.get("consultant_order", [])
    current = [aid for aid in dict.fromkeys([*order, *proposed, *current]) if aid in current]
    current = [*accepted, *[aid for aid in current if aid not in accepted]]
    return current, events, ignored


# Words of a judgment saying that something is not established (any field's wording).
UNESTABLISHED = re.compile(r"non\s+(?:établi|precis|précis|indiqu|spécifi|confirm|démontr|vérifi)\w*|ne\s+(?:précise|indique|"
    r"spécifie|permet|démontre|dit)\s+pas|pas\s+(?:établi|précis|indiqu|spécifi|clair|démontr)\w*|inconnu\w*|incertain\w*|"
    r"unclear|unknown|not\s+(?:established|stated|specified|clear|shown|reported)|ambigu\w*", re.I)


def _stems(text):
    from .store import tokens
    return {t[:6] for t in tokens(text) if len(t) >= 5}


def consistent_criteria(article, relevance, need):
    """Per-criterion judgments made consistent with their own sources, before they rank anything:
    met needs an exact quote of the notice (or of the saved card) that the model cites, never an
    inference; a criterion the same profile says is not established cannot be met. Downgrades go
    to unknown (never not_met, never a rejection) and are listed. Run 111cf83c: NeuralMesh had
    c1 « maillage volumique 3D » met while its own uncertainty said the volumetric nature was not
    stated."""
    if not relevance or not need:
        return relevance, []
    statements = {c["id"]: c["statement"] for c in need.get("criteria", [])}
    def norm(text):
        return " ".join(str(text or "").casefold().split())
    analysis = article.get("analysis") or {}
    sources = {"abstract": norm(article.get("abstract")), "title": norm(article.get("title")),
        "saved_claims": norm(" ".join([*(c.get("statement", "") for v in analysis.values() if isinstance(v, list)
            for c in v if isinstance(c, dict)), *(e.get("exact_quote", "") for e in analysis.get("evidence", []) if isinstance(e, dict))]))}
    sources["fulltext"] = sources["saved_claims"]
    doubts = [*relevance.get("uncertainties", []), *relevance.get("missing_information", [])]
    corrected, changes = [], []
    for check in relevance.get("criteria", []):
        check = dict(check)
        if check.get("status") == "met":
            quote = norm(check.get("evidence"))
            reason = None
            if check.get("basis") == "inference":
                reason = "une inférence n'établit pas le critère"
            elif not quote or quote not in sources.get(check.get("basis"), ""):
                reason = "aucune citation exacte de la source n'établit le critère"
            else:
                words = _stems(statements.get(check.get("criterion_id"), ""))
                for doubt in [check.get("note", ""), *doubts]:
                    if UNESTABLISHED.search(doubt or "") and len(words & _stems(doubt)) >= 2:
                        reason = "le profil déclare lui-même non établi : « " + str(doubt)[:160] + " »"
                        break
            if reason:
                changes.append({"criterion_id": check.get("criterion_id"), "from": "met", "to": "unknown", "reason": reason})
                check.update(status="unknown", downgraded_from="met", downgrade_reason=reason)
        corrected.append(check)
    return {**relevance, "criteria": corrected}, changes


TIER_LABELS = {0: "contribution directe, tous les critères essentiels remplis",
    1: "contribution directe, critères essentiels pas tous établis par la notice",
    2: "contribution connexe ou fondamentale",
    3: "au moins un critère essentiel non rempli"}


def qualification(state, aid):
    """How a candidate answers the need, read from the model's own current judgment of it
    (contribution and per-criterion statuses). The tier orders works, it does not judge them:
    0 direct with every essential criterion met, 1 direct with some not established, 2 related
    or foundational, 3 an essential criterion not met. Never a keyword or access score."""
    article = state.get("articles", {}).get(aid, {})
    assessment = article.get("assessment") or {}
    need = state.get("research_need") or {}
    criteria = {c["id"]: c for c in need.get("criteria", [])}
    essential = [cid for cid, c in criteria.items() if c.get("essential")]
    checks = {c.get("criterion_id"): c for c in (assessment.get("relevance") or {}).get("criteria", [])
        if c.get("criterion_id") in criteria}
    statuses = {cid: checks.get(cid, {}).get("status", "unknown") for cid in essential}
    contribution = assessment.get("contribution")
    if any(status == "not_met" for status in statuses.values()):
        tier = 3
    elif contribution == "direct" and essential and all(status == "met" for status in statuses.values()):
        tier = 0
    elif contribution == "direct":
        tier = 1
    else:
        tier = 2
    current = bool(assessment) and assessment.get("need_signature") == need_signature(state) and \
        assessment.get("source_signature") == candidate_signature(state, aid)
    return {"tier": tier, "label": TIER_LABELS[tier], "current": current, "decision": assessment.get("decision"),
        "contribution": contribution, "essential": statuses,
        "basis": {cid: checks[cid].get("basis") for cid in essential if cid in checks},
        "notes": {cid: checks[cid].get("note", "")[:200] for cid in essential if cid in checks}}


def _comparison_lines(state, incoming, outgoing, q):
    need = {c["id"]: c["statement"] for c in (state.get("research_need") or {}).get("criteria", [])}
    lines = []
    for cid in sorted(set(q[incoming]["essential"]) | set(q[outgoing]["essential"])):
        a, b = q[incoming]["essential"].get(cid, "unknown"), q[outgoing]["essential"].get(cid, "unknown")
        if a != b:
            lines.append(f"{cid} « {need.get(cid, cid)[:120]} » : entrant {a} ({q[incoming]['basis'].get(cid) or '—'}), "
                f"sortant {b} ({q[outgoing]['basis'].get(cid) or '—'})")
    if q[incoming]["contribution"] != q[outgoing]["contribution"]:
        lines.append(f"contribution : entrant {q[incoming]['contribution']}, sortant {q[outgoing]['contribution']}")
    return lines or [f"niveau {q[incoming]['tier']} contre {q[outgoing]['tier']}"]


def rebalance_ranking(state):
    """A retained candidate whose CURRENT judgment by the model places it in a better tier than a
    finalist replaces the finalist of the worst tier (run 04382dc6: TetGAN and 3DMeshNet retained
    as direct volumetric generators, while two works the model itself called related held their
    places because no replacement was ever proposed). Vacant places take the best retained works.
    Within a tier the model's order stands; the consultant's choices prevail; a judgment that is
    not current (need or notice changed) never moves anything. Every change is recorded with the
    criteria compared, their sources, the dependencies and the need, like a proposed replacement."""
    rows = state.get("articles", {})
    evaluation = state.setdefault("evaluation", {})
    current = [aid for aid in evaluation.get("recommended_article_ids", []) if aid in rows]
    held = set(state.get("held_article_ids", []))
    accepted = {aid for aid, a in rows.items() if a.get("selection_override") is True}
    groups = version_groups(state)
    target = state.get("research_progress", {}).get("requested_count")
    position = {aid: i for i, aid in enumerate(rows)}
    challengers = [aid for aid, a in rows.items() if aid not in current and aid not in held
        and a.get("selection_override") is not False and (a.get("assessment") or {}).get("decision") == "retain"]
    q = {aid: qualification(state, aid) for aid in [*current, *challengers]}
    challengers = sorted((aid for aid in challengers if q[aid]["current"] and q[aid]["tier"] < 3),
        key=lambda aid: (q[aid]["tier"], position[aid]))
    title = lambda aid: (rows[aid].get("title") or aid)[:120]
    events = []
    for incoming in challengers:
        if any(groups.get(incoming, incoming) == groups.get(x, x) for x in current):
            continue
        if target and len(current) < target:
            current.append(incoming)
            events.append({"incoming_id": incoming, "outgoing_id": None, "kind": "vacancy_filled",
                "justification": f"Place vacante : {title(incoming)} ({q[incoming]['label']})."})
            continue
        removable = [aid for aid in current if aid not in accepted and q[aid]["current"]]
        if not removable:
            break
        worst = max(removable, key=lambda aid: (q[aid]["tier"], current.index(aid)))
        if q[incoming]["tier"] >= q[worst]["tier"]:
            continue
        current[current.index(worst)] = incoming
        lines = _comparison_lines(state, incoming, worst, q)
        events.append({"incoming_id": incoming, "outgoing_id": worst, "kind": "criteria_dominance",
            "justification": f"{title(incoming)} — {q[incoming]['label']} — répond plus directement au besoin que "
                f"{title(worst)} — {q[worst]['label']}.",
            "information_used": lines[:8],
            "uncertainties": [f"{cid} non établi pour l'entrant" for cid, s in q[incoming]["essential"].items() if s != "met"][:8]})
    progress = state.setdefault("research_progress", {})
    history = progress.setdefault("ranking_changes", [])
    proposals = progress.setdefault("ranking_proposals", {})
    for event in events:
        ids = [aid for aid in (event["incoming_id"], event["outgoing_id"]) if aid]
        event.update(dependencies={aid: candidate_signature(state, aid) for aid in ids}, need_signature=need_signature(state),
            provenance={aid: {"assessment_basis": (rows[aid].get("assessment") or {}).get("basis"),
                "contribution": q[aid]["contribution"], "criteria": q[aid]["essential"], "tier": q[aid]["tier"]} for aid in ids})
        event["id"] = hashlib.sha256(json.dumps(event, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
        if not any(e.get("id") == event["id"] for e in history):
            history.append(event)
        for aid in ids:
            proposals.pop(aid, None)
    # Order by tier, the model's order within a tier; the consultant's order and pins first.
    pinned = [aid for aid in state.get("consultant_order", []) if aid in current]
    rest = [aid for aid in current if aid not in pinned]
    rank = {aid: i for i, aid in enumerate(rest)}
    tier = lambda aid: q[aid]["tier"] if aid in q and q[aid]["current"] else 1
    rest.sort(key=lambda aid: (aid not in accepted, tier(aid), rank[aid]))
    ordered = [*pinned, *rest]
    if ordered != evaluation.get("recommended_article_ids"):
        evaluation["recommended_article_ids"] = ordered
        evaluation["ranking_tiers"] = {aid: q[aid]["tier"] for aid in ordered if aid in q}
    return events


def progress_signature(state):
    policy = state.get("research_progress", {})
    return hashlib.sha256(json.dumps([signature(state), policy.get("article_reviews"),
        [(a["key"], a["status"], a.get("result")) for a in policy.get("actions", [])]],
        sort_keys=True, default=str).encode()).hexdigest()


def triage_pool(state, maximum=16, revisit=()):
    """Round-robin across scientific families/providers; no access/relevance score.
    Every unseen notice remains in the durable corpus, including those without abstracts.
    Pending notices are revisited explicitly, not on every unchanged loop."""
    rows = state.get("articles", {})
    held = set(state.get("held_article_ids", []))
    groups = {}
    for aid, a in sorted(rows.items()):
        if a.get("selection_override") is False or aid in held:
            continue
        assessment = a.get("assessment") or {}
        version = candidate_signature(state, aid)
        progress = state.get("research_progress", {}).get("candidate_progress", {}).get(aid, {})
        abstract_hash = hashlib.sha256(a.get("abstract", "").encode()).hexdigest()
        needs_full_abstract = bool(a.get("abstract") and not a.get("analysis") and
            progress.get("information_level") != "saved_science" and
            (bool(progress) or state.get("full_abstract_receipts", {}).get(aid) != abstract_hash) and
            progress.get("evaluated_full_abstract") != abstract_hash)
        stale = bool(assessment.get("source_signature") and assessment["source_signature"] != version)
        if assessment and aid not in revisit and not needs_full_abstract and not stale:
            continue
        key = tuple(a.get("scientific_families") or a.get("sources") or ["unknown"])
        groups.setdefault(key, []).append(aid)
    # No lexical reordering: measured on the discovery bench (tests/ennoscholar_v4/bench), ordering
    # by the need's words lowered Recall@20 (0.32-0.34 against 0.47 for this round-robin).
    return [group[i] for i in range(max((len(g) for g in groups.values()), default=0))
        for group in groups.values() if i < len(group)][:maximum]


def selection_ids(state, challengers, maximum=20):
    """Reserve the incumbent slots; smaller challenger windows remain resumable."""
    finalists = [aid for aid in state.get("evaluation", {}).get("recommended_article_ids", [])
        if aid in state.get("articles", {}) and state["articles"][aid].get("selection_override") is not False and aid not in state.get("held_article_ids", [])]
    target = state.get("research_progress", {}).get("requested_count") or 5
    incumbents = list(dict.fromkeys(finalists))[:min(target, maximum-1)]
    fresh = [aid for aid in dict.fromkeys(challengers) if aid not in incumbents]
    return [*incumbents, *fresh[:max(1, maximum-len(incumbents))]]


def discovery_signature(state):
    # Reading a body does not erase a discovery checkpoint. New candidates,
    # information resolving a pending notice, or a changed top do invalidate it.
    rows = state.get("articles", {})
    value = [need_signature(state), state.get("evaluation", {}).get("recommended_article_ids", []),
        {aid: [a.get("abstract"), (a.get("assessment") or {}).get("decision"), a.get("selection_override")]
            for aid, a in sorted(rows.items())},
        [(p.get("key"), len(p.get("rows", []))) for p in state.get("research_pending_pages", [])]]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def discovery_current(state):
    receipt = state.get("research_progress", {}).get("discovery_review", {})
    return receipt.get("signature") == discovery_signature(state)


def enqueue_actions(state, proposals, *, maximum_network=None):
    """Use the existing durable queue and operation deduplication across segments."""
    progress = state.get("research_progress", {})
    history = list(progress.get("actions", []))
    basis = signature(state)
    added = []
    for action in proposals:
        data = action.model_dump()
        if any(aid not in state.get("articles", {}) or state["articles"][aid].get("selection_override") is False
                or aid in state.get("held_article_ids", []) for aid in action.article_ids):
            raise ValueError("Action hors corpus ou contraire aux décisions du consultant.")
        if action.kind in {"read", "similar", "triage", "rerank"} and not action.article_ids:
            raise ValueError("Action scientifique sans article cible.")
        if action.kind in {"search", "web", "read"} and not action.query.strip():
            raise ValueError("Action scientifique sans question/requête ciblée.")
        if action.kind == "inspect" and not action.article_ids and not action.query.strip():
            raise ValueError("Inspection bibliographique sans identifiant ni DOI/URL.")
        operation = {k: v for k, v in data.items() if k not in {"reason", "family"}}
        operation["query"] = " ".join(operation["query"].casefold().split())
        operation["sources"] = sorted(set(operation["sources"]))
        operation["article_ids"] = sorted(set(operation["article_ids"]))
        key = hashlib.sha256(json.dumps(operation, sort_keys=True).encode()).hexdigest()
        previous = next((a for a in reversed(history) if a["key"] == key), None)
        if previous and (previous["status"] == "pending" or previous.get("basis") == basis or
                (action.kind != "read" and sum(a["key"] == key for a in history) >= 2)):
            continue
        def notice_is_local(aid):
            article = state.get("articles", {}).get(aid, {})
            return bool(article.get("abstract") or article.get("analysis") or
                any(n.get("kind") == "abstract_lookup" for n in article.get("provider_notices", [])))
        local = action.kind in {"triage", "rerank", "read"} or (action.kind == "inspect" and
            bool(action.article_ids) and all(notice_is_local(aid) for aid in action.article_ids))
        if action.kind == "search":
            local = local_page(state, action.query, action.sources, action.offset, action.limit_per_source) is not None
        if action.kind == "inspect" and action.query and not action.article_ids:
            from .store import ArticleStore
            resolved = ArticleStore(state).resolve(action.query)
            local = bool(resolved) and all(notice_is_local(aid) for aid in resolved)
        used = max(progress.get("discovery_rounds", 0),
            sum(not a.get("local", False) and a.get("discovery_checkpoint", False) for a in history))
        if maximum_network is not None and not local and used >= maximum_network:
            continue
        row = {**data, "key": key, "basis": basis, "status": "pending", "local": local,
            "discovery_checkpoint": maximum_network is not None}
        history.append(row)
        added.append(row)
    # Cheap saved information comes first, without forcing any extra network tool.
    added.sort(key=lambda a: not a["local"])
    state.setdefault("research_progress", {})["actions"] = [*progress.get("actions", []), *added]
    return added


def collect_reference_leads(state):
    """Local bibliography pointers, never articles or proof of their contribution.

    Exact source offsets/hash keep a DOI mention auditable. Only the model decides
    which lead warrants identity verification with the existing get_article tool.
    """
    from .models import normalize_doi
    progress = state.setdefault("research_progress", {})
    leads = progress.setdefault("reference_leads", {})
    scanned = progress.setdefault("reference_scans", {})
    for aid, article in state.get("articles", {}).items():
        text = (article.get("fulltext") or {}).get("text") or ""
        if not text:
            continue
        digest = hashlib.sha256(text.encode()).hexdigest()
        if scanned.get(aid) == digest:
            continue
        scanned[aid] = digest
        headings = list(re.finditer(r"(?im)^\s*(?:#+\s*)?(?:\d+[. ]+)?(?:references|bibliography|références)\s*$", text))
        numbered = list(re.finditer(r"(?m)^\s*\[(\d+)\]\s+\S", text))
        numbered_start = numbered[0].start() if len(numbered) >= 3 and [m[1] for m in numbered[:3]] == ["1", "2", "3"] else len(text)
        start = headings[-1].end() if headings else numbered_start
        for match in list(re.finditer(r"10\.\d{4,9}/[^\s<>\"{}]+", text[start:], re.I))[:100]:
            begin, end = start+match.start(), start+match.end()
            literal = match.group().rstrip(".,;")
            while literal.endswith((")", "]")) and ((literal.endswith(")") and literal.count(")") > literal.count("(")) or
                    (literal.endswith("]") and literal.count("]") > literal.count("["))):
                literal = literal[:-1]
            identifier = normalize_doi(literal)
            if not identifier or identifier == article.get("doi"):
                continue
            key = aid+":"+identifier
            known = next((other_id for other_id, other in state["articles"].items() if other.get("doi") == identifier), None)
            verified = bool(known and state["articles"][known].get("verified") and not state["articles"][known].get("metadata_errors"))
            old = leads.get(key, {})
            leads[key] = {"source_article_id": aid, "identifier": identifier,
                "source_quote": text[max(start, text.rfind("\n", start, begin)+1):min(len(text), end+180)],
                "start": begin, "end": end, "source_hash": digest, "identity_verified": verified,
                "status": "verified_identity" if verified else "known_candidate" if known else old.get("status", "unverified"), "article_id": known}
    return leads


def discovery_packet(state):
    from .context import scientific_dossier
    collect_reference_leads(state)
    progress = state.get("research_progress", {})
    top = state.get("evaluation", {}).get("recommended_article_ids", [])
    pending = [aid for aid, a in state.get("articles", {}).items() if (a.get("assessment") or {}).get("decision") == "pending"]
    proposals = progress.get("ranking_proposals", {})
    competitors = list(dict.fromkeys([*proposals, *ranking_candidates(state)]))
    competitors = [aid for aid in competitors if aid not in top and aid in state.get("articles", {}) and
        (state["articles"][aid].get("assessment") or {}).get("decision") == "retain"]
    from .store import query_terms
    terms = query_terms(str(state.get("research_state", {}).get("topic", ""))+" "+str((state.get("turn_goal") or {}).get("understanding", "")))
    leads = [lead for lead in progress.get("reference_leads", {}).values() if lead.get("status") == "unverified"]
    # Retrieval order only. The model judges importance and identity remains unverified.
    leads.sort(key=lambda lead: -len(terms & query_terms(lead.get("source_quote", ""))))
    return {"families_requested": state.get("research_state", {}).get("search_axes", []),
        "remaining_axes": state.get("evaluation", {}).get("remaining_axes", []),
        "finalists": [scientific_dossier(state, aid) for aid in top[:10] if aid in state.get("articles", {})],
        "pending_candidates": [scientific_dossier(state, aid) for aid in pending[:8]], "pending_total": len(pending),
        "challengers": [scientific_dossier(state, aid) for aid in competitors[:8]], "challengers_total": len(competitors),
        "unresolved_ranking_proposals": {aid: proposals[aid] for aid in competitors[:8] if aid in proposals},
        "options": exploration_options(state), "reference_leads": leads[:8], "reference_leads_total": len(leads),
        "previous_review": progress.get("discovery_review"),
        "guidance": DISCOVERY_GUIDANCE}


def reference_inventory_signature(state):
    return hashlib.sha256(json.dumps(sorted(state.get("research_progress", {}).get("reference_leads", {}))).encode()).hexdigest()


DISCOVERY_GUIDANCE = (
    "Revue RAPIDE de découverte avec review_discovery, avant les lectures longues. "
    "Raisons brèves (une phrase), pas une analyse approfondie ni une synthèse du domaine. "
    "Les familles et critères viennent du besoin (objet réellement produit, rôle de la méthode/IA, "
    "conditions, validation et inconnues), jamais d'une taxonomie imposée. "
    "Vérifier le cadrage contre le message original : ne pas élargir l'objectif central pour remplir le nombre demandé. "
    "Vérifier les concurrents prometteurs, les candidats pending et les familles insuffisamment explorées. "
    "Le nombre demandé n'est pas une preuve du meilleur classement. Proposer au plus trois actions utiles "
    "et justifiées, locales d'abord : notices/abstracts/réservoir déjà stockés, puis référence à vérifier, "
    "pagination, requête ciblée, voisins/citations ou web selon la lacune. Ne pas lancer tous les outils. "
    "Les familles de research_need sans recherche (options.planned_families, searched=false) sont explorées par "
    "leur requête ou déclarées non pertinentes/indisponibles avec raison ; les critères essentiels de research_need "
    "disent ce qu'un candidat direct doit produire : une famille absente du top n'est couverte que si un candidat les remplit. "
    "Une référence citée n'est pas une publication vérifiée. ready_for_reading autorise seulement la "
    "lecture des meilleurs candidats provisoires, jamais la complétude scientifique. "
    "Si aucune action nouvelle utile n'est réalisable, donner stop_reason ; ne pas répéter la revue inchangée."
)


def save_discovery_review(state, args, maximum_network=3):
    if args.ready_for_reading and args.actions:
        raise ValueError("Exécuter les actions de découverte avant d'autoriser les lectures longues.")
    if args.ready_for_reading and (triage_pool(state) or not ranking_current(state) or not state.get("evaluation", {}).get("recommended_article_ids")):
        raise ValueError("Présélection non établie : candidats disponibles encore à trier.")
    if args.ready_for_reading and any(f.status == "missing" for f in args.families):
        raise ValueError("Famille manquante : proposer une exploration ciblée ou un arrêt partiel explicite.")
    declared = {_norm_query(f.family): f.status for f in args.families}
    unexplored = [f["family"] for f in planned_families(state) if not f["searched"]
        and declared.get(_norm_query(f["family"])) not in {"not_relevant", "unavailable", "covered"}]
    if args.ready_for_reading and unexplored:
        # A family of the need's own map never searched cannot be skipped silently before reading.
        raise ValueError("Familles planifiées jamais interrogées : " + "; ".join(unexplored[:6]) + ". Lancer leur requête "
            "(action search) ou les déclarer not_relevant/unavailable/covered avec leur raison.")
    for family in args.families:
        if any(aid not in state.get("articles", {}) for aid in family.article_ids):
            raise ValueError("Famille appuyée sur un article inconnu.")
        if family.status == "covered" and not family.article_ids:
            raise ValueError("Couverture d'une famille sans candidat documenté.")
    actions = enqueue_actions(state, args.actions, maximum_network=maximum_network)
    progress = state.setdefault("research_progress", {})
    row = {**args.model_dump(), "signature": discovery_signature(state), "accepted_actions": [a["key"] for a in actions],
        "reference_inventory_signature": reference_inventory_signature(state)}
    if not args.ready_for_reading and not actions and not pending_actions(state) and not args.stop_reason:
        row["stop_reason"] = "Aucune nouvelle exploration utile admise dans le budget de découverte ; couverture partielle."
    if progress.get("discovery_review") != row:
        progress.setdefault("discovery_reviews", []).append(row)
    progress["discovery_review"] = row
    return {"saved": True, "discovery_review": row}


def transition(state):
    """Explicit, durable scientific control loop; existing run guards bound every call."""
    collect_reference_leads(state)
    observed = status(state)
    if observed["complete"]:
        return {"stage": "complete"}
    if observed["partial_stop"] or observed["exhausted"]:
        return {"stage": "partial", "reason": observed["review"].get("stop_reason") or
            observed.get("discovery_review", {}).get("stop_reason") or observed["review"].get("reason")}
    actions = pending_actions(state)
    if actions:
        return {"stage": "action", "action": actions[0]}
    candidates = triage_pool(state)
    if candidates:
        return {"stage": "triage", "article_ids": candidates}
    selected = canonical_selection(state)["ranked_ids"]
    if not selected and not state.get("recent_searches"):
        return {"stage": "discover"}
    if not discovery_current(state):
        if selected and not ranking_current(state):
            return {"stage": "rerank", "article_ids": stale_ranking_candidates(state)[:16]}
        return {"stage": "discovery_review"}
    discovery = state.get("research_progress", {}).get("discovery_review", {})
    if not discovery.get("ready_for_reading"):
        return {"stage": "partial", "reason": discovery.get("stop_reason") or "discovery_incomplete"}
    if not selected:
        return {"stage": "review" if state.get("recent_searches") else "discover"}
    tasks = state.get("scientific_reading", {}).get("tasks", [])
    by_id = {t["article_id"]: t for t in tasks}
    # A coarse reading checkpoint requests a semantic review; it does not assert depth.
    if any(aid not in by_id or not (by_id[aid].get("initial_reading_ready") or
            by_id[aid].get("exhausted") or by_id[aid].get("status") in {"source_unavailable", "needs_review"})
            for aid in selected) and not state.get("scientific_reading", {}).get("blocked_reason"):
        return {"stage": "read"}
    if state.get("research_progress", {}).get("reference_leads") and discovery.get("reference_inventory_signature") != reference_inventory_signature(state):
        return {"stage": "discovery_review", "reason": "new_local_bibliographic_leads"}
    if len(observed["verified_analyzed_ids"]) >= 2 and not comparison_current(state):
        return {"stage": "compare"}
    if not ranking_current(state):
        return {"stage": "rerank", "article_ids": stale_ranking_candidates(state)[:16]}
    return {"stage": "review"}


REVIEW_GUIDANCE = (
    "Revue scientifique obligatoire avec review_research_coverage. Le nombre de fiches ne prouve ni la profondeur ni le meilleur classement. "
    "Évaluer familles, candidats concurrents non examinés, sélection après lecture, méthodes, protocoles, résultats, métriques, comparaisons/ablations et limites. "
    "Fournir article_reviews : type expérimental (methods, experimental_protocol, results, comparisons, limits), théorique (methods, theory, limits), "
    "ou mixed avec les deux. Chaque dimension est verified, absent (constat borné aux passages examinés), unavailable, unread, ambiguous ou not_applicable, "
    "avec reason et evidence_ids exacts. scope=examined_passages reste une absence locale insuffisante ; absent ne permet de conclure que si "
    "scope=explicit_statement (déclaration explicite des auteurs citée) ou available_document (texte disponible intégralement parcouru, non tronqué). "
    "Champ rempli != question scientifique comprise. Ne jamais inventer un résultat ni conclure absent depuis un abstract. "
    "Si une lacune peut être résolue, actions exécutables et motivées : read (article_ids, query ciblant les informations manquantes), triage/rerank, "
    "search (query, sources, offset, limit_per_source, family), similar (article_ids, mode references/citations/similar/alternatives), "
    "web si les fournisseurs ne suffisent pas, inspect (query=DOI/URL ou article_ids) pour vérifier une découverte web ou un abstract complet. "
    "Réutiliser les lectures et comparaisons valides ; ne pas répéter une action inchangée. Les protocoles différents n'autorisent aucun classement numérique. "
    "sufficient exige analysis_sufficient et scientific_selection_justified, sans lacune ni action restante. Sinon poursuivre utilement ou stop_reason explicite "
    "(budget/temps/accès/absence de progrès), résultat partiel, sans prétendre l'exhaustivité."
)


def save_review(state, args, maximum_network=None):
    progress = state.setdefault("research_progress", {"reviews": [], "requested_count": None})
    progress.setdefault("reviews", [])
    # Validate the whole proposal before mutating the durable checkpoint.
    depth = {}
    from .evidence import exact_source_valid
    for item in args.article_reviews:
        article = state.get("articles", {}).get(item.article_id)
        if not article:
            raise ValueError("Article inconnu dans la revue de profondeur.")
        evidence = {**state.get("evidence", {}),
            **{e["evidence_id"]: e for e in (article.get("analysis") or {}).get("evidence", [])}}
        shown = set(state.get("shown_evidence_ids", []))
        task = next((t for t in state.get("scientific_reading", {}).get("tasks", []) if t["article_id"] == item.article_id), {})
        dimensions = {d.dimension: d for d in item.dimensions}
        if len(dimensions) != len(item.dimensions):
            raise ValueError("Dimension dupliquée dans la revue scientifique.")
        required = {"methods", "limits"} | ({"theory"} if item.article_type == "theoretical" else
            {"experimental_protocol", "results", "comparisons"})
        if item.article_type == "mixed":
            required.add("theory")
        for d in item.dimensions:
            if any(eid not in evidence or eid not in shown or evidence[eid].get("article_id") != item.article_id or
                    not exact_source_valid(evidence[eid], state.get("source_snapshots", {})) for eid in d.evidence_ids):
                raise ValueError("Preuve de revue inconnue, ancienne ou attribuée à une autre publication.")
            if d.status in {"verified", "absent"} and (not d.evidence_ids or any(
                    evidence[eid].get("basis") != "fulltext" for eid in d.evidence_ids)):
                raise ValueError("Une dimension vérifiée/absente exige des passages du texte intégral, pas un abstract.")
            if d.status == "absent" and d.scope == "available_document" and (
                    not task.get("exhausted") or article.get("fulltext", {}).get("truncated")):
                raise ValueError("Absence dans le document non établie : passages encore non examinés ou extraction tronquée.")
        if item.sufficient and (item.article_type == "unknown" or not required <= dimensions.keys() or any(
                dimensions[d].status not in {"verified", "absent", "not_applicable"} for d in required) or
                dimensions["methods"].status != "verified"):
            raise ValueError("Profondeur insuffisante : dimensions scientifiques encore inconnues.")
        if item.sufficient and any(d.status == "absent" and d.scope == "examined_passages" for d in item.dimensions):
            raise ValueError("Profondeur insuffisante : absence locale dans les passages examinés, pas dans l'article.")
        depth[item.article_id] = {**item.model_dump(), "card_signature": card_signature(state, [item.article_id])}
    observed = status(state)
    combined = {**progress.get("article_reviews", {}), **depth}
    deep_missing = [aid for aid in canonical_selection(state)["ranked_ids"] if not combined.get(aid, {}).get("sufficient") or
        combined[aid].get("card_signature") != card_signature(state, [aid])]
    if args.sufficient and (args.gaps or args.actions or pending_actions(state) or not observed["target_reached"] or
            not args.analysis_sufficient or not args.scientific_selection_justified or not comparison_current(state) or not ranking_current(state) or
            ((state.get("turn_goal") or {}).get("reading_depth") == "deep" and deep_missing)):
        raise ValueError("Couverture insuffisante : objectif ou analyses vérifiées encore incomplets. "
            "Consigner les lacunes et la prochaine exploration, sans déclarer une réussite complète.")
    if args.exhausted and (args.next_queries or observed["pending_article_ids"] or observed["unexamined_count"] or
            len(state.get("recent_searches", [])) < 2):
        raise ValueError("Épuisement non établi : des recherches proposées ou des possibilités non examinées restent ouvertes.")
    basis = signature(state)
    proposals = list(args.actions)
    if not args.sufficient and not args.stop_reason and not proposals:
        # Execute the scientific review's own unresolved questions, even when
        # the model omitted the redundant actions array. No gap is invented from
        # the mere absence of a claim or metric.
        tasks = {t["article_id"]: t for t in state.get("scientific_reading", {}).get("tasks", [])}
        for item in args.article_reviews:
            task = tasks.get(item.article_id, {})
            unread = [d for d in item.dimensions if d.status in {"unread", "ambiguous"}]
            if unread and task.get("basis") == "fulltext" and task.get("remaining_passages") and not state.get("scientific_reading", {}).get("blocked_reason"):
                proposals.append(ResearchAction(kind="read", article_ids=[item.article_id],
                    query=" ".join(f"{d.dimension} {d.reason}" for d in unread)[:1500],
                    reason="Question scientifique non résolue de la revue de profondeur ; passages encore accessibles."))
    # Legacy next_queries become actual pending work, not a narrative-only promise.
    if args.next_queries and not args.actions:
        raise ValueError("Prochaines requêtes : fournir des actions search exécutables.")
    actions = enqueue_actions(state, proposals, maximum_network=maximum_network)
    progress["article_reviews"] = combined
    row = {**args.model_dump(), "signature": basis}
    row["actions"] = [a.model_dump() for a in proposals]
    if not args.sufficient and not actions and not pending_actions(state) and not row.get("stop_reason"):
        row["stop_reason"] = "Aucune action nouvelle réalisable proposée ; recherche partielle, lacunes conservées."
    if not progress["reviews"] or progress["reviews"][-1] != row:
        progress["reviews"].append(row)
    return {"saved": True, "research_coverage": status(state)}


def exploration_options(state, now=None):
    """Concrete next steps read from the stored searches, in any field: pages a provider
    filled completely (a next page may exist and is not collected yet), providers
    temporarily unavailable (web search then serves as fallback, its results verified with
    get_article), and families already searched (reformulate rather than repeat)."""
    now = time.time() if now is None else now
    searches = state.get("recent_searches", [])
    keys = [s.get("key") for s in searches]
    pages = []
    for search in searches:
        key = search.get("key") or []
        if len(key) != 4:
            continue
        query, sources, offset, limit = key
        full = sorted(name for name, row in (search.get("providers") or {}).items()
            if isinstance(row, dict) and isinstance(row.get("returned"), int) and row["returned"] >= limit
            and not any(s.get("query") == query and (s.get("key") or [None, None, 0, 0])[2:] == [offset+limit, limit]
                and isinstance(s.get("providers", {}).get(name, {}).get("returned"), int)
                for s in searches))
        if full and not any(p["query"] == query and p["next_offset"] == offset + limit for p in pages):
            pages.append({"query": query, "sources": full, "next_offset": offset + limit, "limit_per_source": limit,
                "family": search.get("family")})
    unavailable = sorted(name for name, health in state.get("research_provider_health", {}).items()
        if health.get("retry_at", 0) > now)
    partial = [{"query": s.get("query"), "sources": (s.get("key") or [None, [], 0])[1], "offset": (s.get("key") or [None, None, 0])[2],
        "limit_per_source": (s.get("key") or [None, None, 0, 12])[3], "not_stored": s.get("limit_dropped"),
        "local_resume": any(p.get("key") == s.get("key") and p.get("rows") for p in state.get("research_pending_pages", []))}
        for s in searches if s.get("limit_dropped")]
    return {"next_pages": pages[:8], "partial_pages": partial[-8:], "providers_unavailable": unavailable,
        "planned_families": planned_families(state),
        "families_searched": sorted({s.get("family") for s in searches if s.get("family")}),
        "queries_collected": [s.get("query") for s in searches][-12:],
        "web_search_fallback": bool(unavailable) or not searches}


def _norm_query(text):
    return " ".join(str(text or "").casefold().split())


def planned_families(state):
    """The need's family map against the stored searches: a family counts as explored once
    its own query (or a search labelled with its name) was collected, whatever the provider."""
    searches = state.get("recent_searches", [])
    done_queries = {_norm_query(s.get("query")) for s in searches}
    done_families = {_norm_query(s.get("family")) for s in searches if s.get("family")}
    return [{"family": f["family"], "query": f["query"],
        "searched": _norm_query(f["query"]) in done_queries or _norm_query(f["family"]) in done_families}
        for f in (state.get("research_need") or {}).get("families", [])]


def local_page(state, query, sources, offset, limit):
    """A partial provider page can be drained by its original or a subset request."""
    requested = set(sources or ["semantic_scholar", "openalex", "arxiv"])
    for page in state.get("research_pending_pages", []):
        key = page.get("key", [])
        if len(key) != 4 or " ".join(key[0].split()).casefold() != " ".join(query.split()).casefold() or key[2:] != [offset, limit]:
            continue
        if requested <= set(key[1]) and any(requested & set(row.get("sources") or key[1]) for row in page.get("rows", [])):
            return page
    return None


def needs_discovery(state):
    observed = status(state)
    return not (observed["complete"] or observed["exhausted"] or observed["partial_stop"]) and (
        bool(observed["gaps"] or observed["unexamined_count"]) or
        any(a["kind"] in {"search", "similar", "web"} for a in observed["pending_actions"]))


def source_plan(state, query, sources, offset, limit, now=None):
    now = time.time() if now is None else now
    query = " ".join(query.split())
    planned = []
    for source in sources or ["semantic_scholar", "openalex", "arxiv"]:
        health = state.get("research_provider_health", {}).get(source, {})
        if health.get("retry_at", 0) > now:
            continue
        done = any(" ".join(s.get("query", "").split()).casefold() == query.casefold() and (s.get("key") or [None, None, 0, 0])[2:] == [offset, limit]
            and isinstance(s.get("providers", {}).get(source, {}).get("returned"), int)
            for s in state.get("recent_searches", []))
        if not done:
            planned.append(source)
    return planned


def record_providers(state, warnings, stats):
    health = state.setdefault("research_provider_health", {})
    for name, row in stats.items():
        if isinstance(row.get("returned"), int):
            health[name] = {"failures": 0, "retry_at": 0}
    for warning in warnings:
        name = warning.get("source")
        if not name:
            continue
        failures = health.get(name, {}).get("failures", 0) + 1
        try:
            delay = float(warning.get("retry_after") or min(120, 5 * 2 ** min(failures, 4)))
        except (TypeError, ValueError):
            delay = 30
        health[name] = {"failures": failures, "retry_at": time.time() + max(1, min(300, delay)),
            "last_error": warning.get("error"), "http_status": warning.get("http_status")}


def reading_seconds(durations, default=60):
    values = [seconds for seconds, output in durations if output >= 500]
    return min(120.0, max(20.0, max(values[-6:]) * 1.35 + 5)) if values else float(default)


def record_context_block(state, details):
    """A selection batch that cannot fit after every safe reduction: its signature (batch,
    notices, need, room) is stored, so that a continuation is not started into the same
    refusal; an unchanged corpus would fail identically."""
    ids = sorted(set([*details.get("incumbent_ids", []), *details.get("candidate_ids", [])]))
    signature = hashlib.sha256(json.dumps([ids, {aid: candidate_signature(state, aid) for aid in ids if aid in state.get("articles", {})},
        need_signature(state), details.get("budget")], sort_keys=True, default=str).encode()).hexdigest()
    block = {"signature": signature, "size": details.get("size"), "budget": details.get("budget"),
        "reductions": details.get("reductions", []), "article_ids": ids,
        "parts": dict(sorted((details.get("parts") or {}).items(), key=lambda kv: -kv[1])[:8]),
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    state.setdefault("research_progress", {})["selection_context_block"] = block
    return block


def job_evaluate(job, state, run_id, run_status, run_data):
    """Research policy on the existing job envelope, lease, driver and per-call ledger."""
    from .writing_job import stop, can_start_segment, now, NON_RETRYABLE, INTERRUPTED
    from .job_ledger import totals, add
    if job.get("current_run_id") != run_id or any(s["run_id"] == run_id for s in job["segments"]):
        return job
    spent = totals(run_data.get("ledger"), run_over=True)
    observed = status(state)
    sig = progress_signature(state)
    job["spent"] = add(job["spent"], spent)
    job["segments"].append({"run_id": run_id, "status": run_status, "reason": run_data.get("reason"),
        "spent": spent, "analyzed_count": observed["verified_analyzed_count"], "finished_at": now()})
    job.update(current_run_id=None, updated_at=now(), requested_count=observed["requested_count"])
    if state.get("turn_goal"):
        copy_goal = json.loads(json.dumps(state["turn_goal"]))
        if job.get("goal") and job["goal"] != copy_goal:
            return stop(job, "failed", error="Objectif de recherche modifié pendant une continuation ; arrêt explicite.")
        if not job.get("goal"):
            job["goal"] = copy_goal
        if copy_goal.get("deliverable") != "literature_search" or copy_goal.get("search_needed") is False:
            return stop(job, "not_applicable", error=None)
    progress = state.get("research_progress")
    if progress:
        job["progress"] = {"id": progress["id"], "request_message_id": progress.get("request_message_id")}
    job["initializing"] = not bool(job.get("progress"))
    reason = run_data.get("reason")
    if run_status == "cancelled" or reason == "cancelled" or job.get("cancel_requested"):
        return stop(job, "cancelled")
    if reason in NON_RETRYABLE:
        return stop(job, "failed", error="Recherche arrêtée sur incident technique ; candidats et preuves conservés.", last_cause=reason)
    if reason == "selection_context_blocked":
        # Deterministic, not an interruption: a continuation on the same corpus would build the same
        # oversized batch. Explicit stop at once, the cause and sizes kept (run 111cf83c: two identical
        # continuations without a model call).
        block = (state.get("research_progress") or {}).get("selection_context_block") or {}
        return stop(job, "stalled", error="Recherche arrêtée : le lot de comparaison (finalistes et concurrent) dépasse le "
            f"contexte disponible ({block.get('size')} caractères pour {block.get('budget')}) après toutes les réductions "
            "sûres ; aucune reprise identique n'est lancée. Candidats, décisions et classement sont conservés.",
            last_cause=reason, context_block=block.get("signature"))
    if observed["complete"]:
        return stop(job, "completed")
    if observed.get("partial_stop"):
        return stop(job, "exhausted", error="Recherche partielle : " + str(observed["review"].get("stop_reason") or
            observed.get("discovery_review", {}).get("stop_reason")))
    if observed["exhausted"]:
        return stop(job, "exhausted", error="Recherche terminée avec couverture partielle : " + str(observed["review"].get("reason")))
    technical = reason in INTERRUPTED or run_status == "failed"
    job["technical_failures"] = job.get("technical_failures", 0) + 1 if technical else 0
    job["no_progress"] = job.get("no_progress", 0) + 1 if sig == job.get("corpus_signature") and not technical else 0
    job["corpus_signature"] = sig
    if job["technical_failures"] >= job["stalled_segments"] or job["no_progress"] >= job["stalled_segments"]:
        return stop(job, "stalled")
    allowed, stop_status = can_start_segment(job)
    if not allowed or reason == "budget_refused":
        return stop(job, stop_status or "budget_exhausted")
    job["status"] = "waiting"
    job["error"] = "Recherche partielle conservée ; poursuite automatique avec le même budget global."
    return job
