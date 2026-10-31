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
    essential: bool = Field(description="true : une publication qui ne la remplit pas ne répond pas directement au besoin. "
        "Tout élément qui départage une contribution directe d'une contribution connexe est un critère essentiel.")


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
    query: str = Field(default="", max_length=300, description="status missing : requête bibliographique en anglais "
        "pour combler cette famille, formulée avec la terminologie observée dans les résultats pertinents (pas les mots "
        "d'une requête déjà envoyée) ; elle est lancée comme recherche complémentaire. \"\" sinon.")


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
    text = message or ""
    # Prefer an explicit target over a historical count (« déjà 7 … total 10 »).
    target = re.search(r"\b(?:jusqu['’]à|objectif(?:\s+de)?|total(?:\s+de)?|s[ée]lection\s+(?:de|[àa]))\s+(.+)", text, re.I)
    if target:
        count = requested_count(target[1])
        if count:
            return count
    text = re.sub(r"\b(?:(?:nous\s+)?avons\s+(?:d[ée]j[àa]\s+)?|d[ée]j[àa]\s+)\d+\s+articles?\b", "", text, flags=re.I)
    match = re.search(r"\b(\d+(?:[ \u00a0\u202f]\d{3})*)\s+(?:(?:meilleurs?|best|scientific|scientifiques?|most\s+relevant)\s+)*"
        r"(?:articles?|publications?|papers?|references?|références?|travaux)\b", text, re.I)
    count = int(re.sub(r"\s", "", match[1])) if match else None
    if count is None:
        words = {"un": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7,
            "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13,
            "quatorze": 14, "quinze": 15, "seize": 16, "vingt": 20, "trente": 30, "cinquante": 50, "cent": 100}
        match = re.search(r"\b("+"|".join(words)+r")\s+(?:(?:meilleurs?|scientifiques?|plus\s+pertinents?)\s+)*"
            r"(?:articles?|publications?|travaux)\b", text, re.I)
        count = words[match[1].casefold()] if match else None
    return count if count and count > 0 else None


def original_progress(state, message=None):
    """Count the original engine's durable decisions; never execute its controller."""
    progress = state.setdefault("research_progress", {})
    if message:
        text = message.get("content", "")
        count = requested_count(text)
        continuation = bool(re.search(r"\b(?:poursui\w*|continu\w*|reprends|repren\w*|compl[ée]t\w*|manquants?|autres)\b", text, re.I))
        from .references import opens_literature_search
        if opens_literature_search(text) and progress.get("selection_message_id") != message.get("id"):
            if not continuation and progress.get("request_message_id") != message.get("id"):
                progress["proposed_article_ids"] = []
            elif "proposed_article_ids" not in progress:
                progress["proposed_article_ids"] = list(dict.fromkeys([
                    *(aid for h in state.get("response_history", [])[-2:] for aid in h.get("displayed_article_ids", [])),
                    *state.get("displayed_article_ids", [])]))
            progress["selection_message_id"] = message.get("id")
            progress["previous_proposed_count"] = len(progress.get("proposed_article_ids", []))
        goal = state.get("turn_goal") or {}
        if (goal.get("references") or {}).get("message_id") == message.get("id") and goal.get("deliverable") == "literature_search":
            count = goal.get("requested_article_count") or count
        if count is not None and (not state.get("conversation_references", {}).get("article_ids") or
                wants_research(text) or re.search(r"meilleurs?|best|identif", text, re.I)):
            progress.update(requested_count=count, request_message_id=message.get("id"))
    rows = state.get("articles", {})
    examined = set(state.get("examined_article_ids", []))
    held = set(state.get("held_article_ids", []))
    def works(ids):
        return {rows[i].get("canonical_work_id") or i for i in ids if i in rows}
    eligible = lambda i: i in rows and i not in held and rows[i].get("selection_override") is not False
    retained = [i for i, a in rows.items() if eligible(i) and (a.get("assessment") or {}).get("decision") == "retain"]
    proposed = [i for i in progress.get("proposed_article_ids", state.get("displayed_article_ids", [])) if eligible(i)
        and (rows[i].get("selection_override") is True or (rows[i].get("assessment") or {}).get("decision") != "reject")]
    accepted = [i for i, a in rows.items() if eligible(i) and a.get("selection_override") is True]
    judged = [i for i, a in rows.items() if (a.get("assessment") or {}).get("decision") in {"retain", "reject", "pending"}]
    target = progress.get("requested_count")
    counts = {"requested_count": target, "found_count": len(works(rows)),
        "evaluated_count": len(works(examined)), "judged_count": len(works(judged)),
        "retained_count": len(works(retained)), "proposed_count": len(works(proposed)),
        "consultant_accepted_count": len(works(accepted)),
        "unexamined_count": len(set(rows)-examined),
        "pending_count": sum((a.get("assessment") or {}).get("decision") == "pending" for a in rows.values())}
    # Remaining publications to propose, not missing human approvals or scientific judgments.
    counts["missing_count"] = max(0, target-counts["proposed_count"]) if target is not None else None
    analysed = [i for i in proposed if rows[i].get("analysis")]
    proposed_works = works(proposed)
    counts.update(analysed_proposed_count=len(works(analysed)),
        proposed_without_analysis_count=len(works(proposed)-works(analysed)),
        retained_unproposed_count=len(works(retained)-proposed_works),
        selection_complete=target is not None and counts["proposed_count"] >= target)
    shown = set(state.get("shown_evidence_ids", []))
    read = {e.get("article_id") for eid, e in state.get("evidence", {}).items() if eid in shown}
    counts.update(previous_proposed_count=progress.get("previous_proposed_count", counts["proposed_count"]),
        added_count=max(0, counts["proposed_count"]-progress.get("previous_proposed_count", counts["proposed_count"])),
        read_count=len(works(read & set(proposed))), saved_analysis_count=len(works(analysed)),
        abstract_analysis_count=len(works(i for i in analysed if rows[i].get("analysis_basis") == "abstract")),
        fulltext_analysis_count=len(works(i for i in analysed if rows[i].get("analysis_basis") == "fulltext")))
    progress.update(counts)
    return counts


def record_original_proposals(state, ids):
    """Cumulative work selection, separate from this answer's displayed list."""
    if state.get("discovery_mode") != "original":
        return
    from .instructions import current_request
    from .references import opens_literature_search
    message = current_request(state) or {}
    goal = state.get("turn_goal") or {}
    if not (opens_literature_search(message.get("content", "")) or goal.get("deliverable") == "literature_search"):
        return
    progress = state.setdefault("research_progress", {})
    progress["proposed_article_ids"] = list(dict.fromkeys([
        *progress.get("proposed_article_ids", state.get("displayed_article_ids", [])), *ids]))


def original_obligations(state, toolkit):
    """Current research obligations from durable receipts, not a controller or score.

    Downloaded text is not a read; a delivered passage is not a saved analysis.
    A follow-up can resume these IDs without another download or extraction.
    """
    from .instructions import current_request
    from .references import opens_literature_search
    from .evidence import exact_source_valid
    request = current_request(state) or {}
    text = request.get("content", "")
    counts = original_progress(state)
    goal = state.get("turn_goal") or {}
    declared = goal if (goal.get("references") or {}).get("message_id") == request.get("id") else {}
    search = (declared.get("deliverable") == "literature_search" and declared.get("search_needed") is not False) if declared.get("deliverable") else opens_literature_search(text)
    active = bool(counts["requested_count"] and search)
    progress = state["research_progress"]
    initial = next((m.get("content", "") for m in state.get("messages", [])
        if m.get("id") == progress.get("request_message_id")), "")
    analyse = active and (declared.get("reading_depth") in {"core", "deep"} or
        bool(re.search(r"analys|compar|approfond|détaill|detail", text+" "+initial, re.I)))
    refs = state.get("conversation_references") or {}
    refs = declared.get("references") or refs
    targeted = refs.get("article_ids") if refs.get("scope") == "exclusive" and refs.get("message_id") in {None, request.get("id")} else []
    resume_ids = []
    if active and not targeted:
        # Older runs have no pending markers: reconstruct their unfinished reads
        # from the published works and exact receipts, without changing the DB.
        resume_ids = [*progress.get("pending_analysis_ids", []), *progress.get("remaining_candidate_ids", []),
            *progress.get("proposed_article_ids", state.get("displayed_article_ids", [])),
            *(t["article_id"] for t in state.get("scientific_reading", {}).get("tasks", []) if t.get("status") == "queued")]
        signature = need_signature(state)
        resume_ids.extend(aid for aid, a in toolkit.registry.items() if a.assessment.get("decision") == "retain"
            and a.assessment.get("need_signature") == signature)
    ids = list(dict.fromkeys([*state.get("current_turn_article_ids", []), *resume_ids]))
    pending = []
    shown = set(toolkit.shown_evidence)
    for aid in ids:
        article = toolkit.registry.get(aid)
        if not analyse or not article or article.analysis or article.selection_override is False:
            continue
        proofs = [e for eid, e in toolkit.evidence.items() if eid in shown and e["article_id"] == aid
            and e.get("basis") in {"fulltext", "abstract"} and exact_source_valid(e, toolkit.snapshots)]
        # Documentary ordering only: a method/result passage before an introduction.
        proofs.sort(key=lambda e: (not bool(re.search(r"results?|methods?|méthod|experiment", e.get("section", ""), re.I)),
            len(e.get("exact_quote", ""))))
        if proofs:
            pending.append({"article_id": aid, "evidence_ids": [e["evidence_id"] for e in proofs],
                "basis": "fulltext" if all(e.get("basis") == "fulltext" for e in proofs) else "abstract",
                "scope": "shown_passages_only"})
    proposed = {toolkit.registry[aid].canonical_work_id or aid for aid in progress.get("proposed_article_ids", state.get("displayed_article_ids", []))
        if aid in toolkit.registry}
    candidates = [{"article_id": aid, "verified": a.verified, "metadata_errors": a.metadata_errors,
        "needs_reassessment": not need_current(state, a.assessment.get("need_signature"))}
        for aid in ids if (a := toolkit.registry.get(aid)) and a.selection_override is not False
        and a.assessment.get("decision") == "retain" and (a.canonical_work_id or aid) not in proposed]
    if active:
        progress["pending_analysis_ids"] = [r["article_id"] for r in pending]
        progress["remaining_candidate_ids"] = [r["article_id"] for r in candidates]
        progress["remaining_reading_ids"] = [t["article_id"] for t in state.get("scientific_reading", {}).get("tasks", [])
            if t["article_id"] in ids and t.get("status") in {"queued", "partial", "awaiting_analysis", "source_unavailable", "needs_review"}]
    return {"active": active, "analysis_requested": analyse, "requested_count": counts["requested_count"],
        "previously_proposed_count": counts["proposed_count"], "missing_count": counts["missing_count"],
        "unexamined_count": counts["unexamined_count"], "pending_analyses": pending,
        "retained_unproposed": candidates}


def original_completion(state, toolkit):
    """The existing original-mode ledger, also used after a writing handoff.

    A technical publication may succeed while its consultant request remains
    partial. This never refuses or fills a scientific selection.
    """
    obligations = original_obligations(state, toolkit)
    counts = original_progress(state)
    if obligations["active"]:
        state["research_progress"]["request_complete"] = bool(counts["selection_complete"] and not (
            obligations["analysis_requested"] and counts["proposed_without_analysis_count"]))
    return {**counts, **{k: state["research_progress"][k] for k in (
        "pending_analysis_ids", "remaining_candidate_ids", "remaining_reading_ids") if k in state["research_progress"]},
        **({"request_complete": state["research_progress"]["request_complete"]} if obligations["active"] else {})}


def wants_research(message):
    text = message or ""
    if re.search(r"aucune\s+(?:nouvelle\s+)?recherche|sans\s+(?:nouvelle\s+)?recherche|"
            r"n['’]effectue\s+(?:aucune|pas)|no\s+(?:new\s+)?search", text, re.I):
        return False
    return bool(re.search(r"\b(?:recherch\w*|cherch\w*|trouv\w*|search|find|identify)\b", text, re.I)
        and re.search(r"articles?|publications?|papers?|littérature|literature|travaux", text, re.I))


def active(state):
    # discovery_mode "original" (recorded from the configuration at each run): the original engine of 2026-10-13
    # conducts discovery and selection; this controller stays inactive. A state without the flag (stored before
    # 2026-10-23, or worked directly by the research functions) keeps the controller.
    if state.get("discovery_mode") == "original":
        return False
    goal = state.get("turn_goal") or {}
    return goal.get("deliverable") == "literature_search" and goal.get("search_needed") is not False \
        and "no_search" not in (goal.get("constraints") or [])


def begin(state, message, message_id=None, *, preserve_progress=False):
    progress = state.get("research_progress")
    continuation = bool(progress and preserve_progress and state.get("discovery_mode") == "original" and
        re.search(r"\b(?:poursui\w*|continu\w*|reprends|repren\w*|compl[ée]t\w*|manquants?|autres)\b", message or "", re.I))
    if not progress or progress.get("request_message_id") != message_id and not continuation:
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
    # A negative discovery review stops the research only when there is nothing to read; with a provisional
    # preselection, transition() reads it (coverage reported partial), see « discovery_closed ».
    discovery_stop = bool(discovery_current(state) and not discovery.get("ready_for_reading") and discovery.get("stop_reason")
        and not pending_actions(state) and (discovery.get("stop_origin") == "model" or not selection["ranked_ids"]))
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
        # Deferred notices are not rejected. The per-family coverage travels with the discovery review
        # (discovery_packet, saved review), not with every request's context.
        **({"deferred_candidates_count": discovery["deferred_candidates"]["count"]}
            if discovery.get("ready_for_reading") and discovery.get("deferred_candidates") else {}),
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


def legacy_need_signature(state):
    """Signature used before the stable need (kept so that receipts written with it stay readable)."""
    goal = state.get("turn_goal") or {}
    value = [state.get("scientific_scope"),
        {k: state.get("research_state", {}).get(k) for k in ("topic", "scientific_question", "objectives", "exclusions")},
        {k: goal.get(k) for k in ("understanding", "article_scope", "article_ids", "constraints")}]
    if state.get("research_need"):
        value.append({k: v for k, v in state["research_need"].items() if k not in {"families", "uncertainties"}})
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def need_signature(state):
    """What every relevance judgment depends on. With a structured need (research_need), only the stable
    scientific need counts: object, problem, expected output, acceptable methods, context, constraints,
    criteria and the definitions of direct/related/foundational, plus the request's explicit scope and
    constraints. Editorial wording (scientific_scope, research_state summaries, the goal's understanding
    sentence) does not: real trial 02098ebc reworded them, research_need unchanged, and 22 candidates were
    judged again on identical notices. A new message re-declaring the need still invalidates (research_need
    changes). Without a structured need (older sessions) the previous signature applies."""
    if not state.get("research_need"):
        return legacy_need_signature(state)
    goal = state.get("turn_goal") or {}
    value = ["stable_need_v1", {k: v for k, v in state["research_need"].items() if k not in {"families", "uncertainties"}},
        {k: goal.get(k) for k in ("article_scope", "article_ids", "constraints")}]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def need_current(state, value):
    """A receipt's need signature still describes the current need (stable form, or the legacy form of a
    receipt written before it when nothing at all has changed since)."""
    return bool(value) and value in {need_signature(state), legacy_need_signature(state)}


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
    needs = {need_signature(state), legacy_need_signature(state)}
    return [aid for aid in candidates if records.get(aid, {}).get("need_signature") not in needs or
        any(candidate_signature(state, dep) != version for dep, version in records.get(aid, {}).get("dependencies", {}).items()) or
        aid not in records.get(aid, {}).get("dependencies", {})]


def stale_judged(state):
    """Judged candidates whose ranking receipt is outdated. A notice never judged is triage still to do,
    not a ranking to revise: counting it here made « all notices triaged » a condition of every
    reading (run 9c7625c8)."""
    rows = state.get("articles", {})
    tasks = {t["article_id"]: t for t in state.get("scientific_reading", {}).get("tasks", [])}
    # A card being read changes at every pass: the finalists read so far are revised together once the
    # initial readings of the preselection end (complete cards, one request), not after each pass or each
    # article (rejeu 9c7625c8 : 15 reclassements intermédiaires d'un ou deux articles).
    selected = canonical_selection(state)["ranked_ids"]
    reading = set() if initial_readings_done(state, selected) else {aid for aid, task in tasks.items()
        if task.get("examined_evidence_ids")}
    return [aid for aid in stale_ranking_candidates(state) if (rows[aid].get("assessment") or {}).get("decision")
        and aid not in reading]


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
    needs = {need_signature(state), legacy_need_signature(state)}
    reviewed = [aid for aid in records if records[aid].get("need_signature") in needs and
        all(candidate_signature(state, dep) == version for dep, version in records[aid]["dependencies"].items())]
    row = {"article_ids": list(state.get("evaluation", {}).get("recommended_article_ids", [])),
        "decisions": decisions, "reviewed_ids": reviewed,
        "card_signature": card_signature(state), "corpus_ids": sorted(state.get("articles", {}))}
    progress.setdefault("ranking_history", []).append(row)
    progress["ranking_review"] = row
    return row


def cumulative_ranking(state, proposed, replacements, verdicts=None, *, require_dossiers=True):
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
    if not require_dossiers:
        # Original conversational engine: compare its actually presented notices,
        # without activating selection dossiers or the research controller.
        target = max(len(previous), len(proposed), len(accepted))
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
            "comparison_notice_not_presented" if not require_dossiers and any(
                aid not in state.get("exposed_article_ids", []) for aid in ids) else
            "comparison_dossier_not_received" if require_dossiers and any(receipts.get(aid, {}).get("version") != candidate_signature(state, aid) or
                not need_current(state, receipts.get(aid, {}).get("need_signature"))
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
            "need_signature": need_signature(state), "provenance": {aid: receipts[aid] if require_dossiers else
                {"basis": (rows[aid].get("assessment") or {}).get("basis", "presented_notice"),
                 "source_signature": candidate_signature(state, aid)} for aid in ids}}
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


# Enclosing quotation marks a model puts around a quote; only a matching pair at both ends is removed.
QUOTE_PAIRS = {'"': '"', "'": "'", "«": "»", "“": "”", "‘": "’", "„": "“", "‹": "›", "`": "`"}


def unwrap_quote(text):
    """The quote itself, without the quotation marks enclosing it (« … », "…", “…”). Nothing else
    changes: an altered quote stays altered and is still refused by the exact-match check."""
    value = str(text or "").strip()
    while len(value) >= 2 and QUOTE_PAIRS.get(value[0]) == value[-1]:
        value = value[1:-1].strip()
    return value


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
            # Run 9c7625c8 audit: an exact quote wrapped in « » or "" was refused for its quotation marks alone.
            quote = norm(unwrap_quote(check.get("evidence")))
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
    current = bool(assessment) and need_current(state, assessment.get("need_signature")) and \
        assessment.get("source_signature") == candidate_signature(state, aid)
    return {"tier": tier, "label": TIER_LABELS[tier], "current": current, "decision": assessment.get("decision"),
        "contribution": contribution, "essential": statuses,
        "basis": {cid: checks[cid].get("basis") for cid in essential if cid in checks},
        "notes": {cid: checks[cid].get("note", "")[:200] for cid in essential if cid in checks}}


def contribution_consistency(state, assessment):
    """The model's contribution next to its own essential-criteria judgments (one definition of relevance:
    research_need's). Real trial 02098ebc: TetGAN met both essential criteria and stayed « related » because of
    a non-essential mechanical dimension. Nothing is rewritten: a work may be related while meeting criteria, but
    then the deciding element must be stated; a « direct » work failing an essential criterion is tier 3."""
    need = state.get("research_need") or {}
    essential = [c["id"] for c in need.get("criteria", []) if c.get("essential")]
    relevance = assessment.get("relevance") or {}
    if not essential or not relevance or assessment.get("decision") == "reject":
        return None
    checks = {c.get("criterion_id"): c.get("status") for c in relevance.get("criteria", [])}
    statuses = {cid: checks.get(cid, "unknown") for cid in essential}
    contribution = assessment.get("contribution")
    if contribution == "direct" and "not_met" in statuses.values():
        return {"kind": "direct_with_unmet_essential", "criteria": [cid for cid, s in statuses.items() if s == "not_met"],
            "message": "Contribution déclarée directe alors qu'un critère essentiel n'est pas rempli : niveau 3 tant que "
                "ce jugement n'est pas révisé."}
    if contribution in {"related", "foundational"} and all(s == "met" for s in statuses.values()):
        return {"kind": "essentials_met_but_" + contribution, "fit": str(relevance.get("fit") or "")[:300],
            "message": f"Tous les critères essentiels sont remplis mais le travail est jugé {contribution} : relevance.fit "
                "doit nommer l'élément du besoin qui manque ; si cet élément décide de la pertinence directe, il relève "
                "d'un critère essentiel. À confirmer ou réviser à la lecture."}
    return None


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


def selection_ids(state, challengers, maximum=20, max_output_tokens=None):
    """Reserve the incumbent slots; smaller challenger windows remain resumable."""
    return selection_batch(state, challengers, maximum, max_output_tokens)["candidate_ids"]


# Output budget of one selection request. Run 9c7625c8: 15 new decisions, each with a full relevance
# profile (1 900-2 900 characters, about 4.3 characters per token), plus 1 000-2 200 reasoning tokens,
# reached max_output_tokens=7000 three times; the truncated JSON saved nothing and the continuations
# sent the same 20 dossiers again. Priors are used until real receipts of this research exist.
DECISION_TOKENS_PRIOR = 500
REASONING_TOKENS_PRIOR = 2200
SELECTION_OVERHEAD_TOKENS = 400
OUTPUT_SAFETY = 0.85


def selection_output(state):
    return state.setdefault("research_progress", {}).setdefault("selection_output", {"receipts": [], "cap": None})


def selection_batch_size(state, max_output_tokens, maximum=20):
    """How many decisions one response can hold completely: the output budget minus the measured
    reasoning and fixed parts, divided by the measured size of one decision. A truncated batch
    caps the next one at half its size until a response completes."""
    record = (state.get("research_progress") or {}).get("selection_output") or {}
    receipts = record.get("receipts", [])
    done = [r for r in receipts if r.get("status") == "completed" and r.get("returned")][-6:]
    failed = [r for r in receipts if r.get("status") == "incomplete" and r.get("requested")][-6:]
    observed = max([r.get("reasoning_tokens") or 0 for r in receipts[-6:]], default=0)*1.25
    reasoning = max(800, observed) if len(done) >= 2 else max(REASONING_TOKENS_PRIOR, observed)
    per =[max(1, (r["output_tokens"]-(r.get("reasoning_tokens") or 0)-SELECTION_OVERHEAD_TOKENS)/r["returned"]) for r in done]
    # A truncated response proves that one decision of that batch cost at least this much.
    floor = [max(1, (r["output_tokens"]-(r.get("reasoning_tokens") or 0)-SELECTION_OVERHEAD_TOKENS)/r["requested"]) for r in failed]
    decision = max([*(p*1.15 for p in per), *floor]) if len(done) >= 2 else max([DECISION_TOKENS_PRIOR, *floor])
    room = max_output_tokens*OUTPUT_SAFETY-reasoning-SELECTION_OVERHEAD_TOKENS
    size = int(room // decision) if room > 0 else 1
    if record.get("cap"):
        size = min(size, record["cap"])
    if record.get("ceiling"):
        # A size already cut by the output limit is never requested again in this research.
        size = min(size, record["ceiling"]-1)
    return max(1, min(maximum, size))


def record_selection_output(state, *, requested_ids, returned, output_tokens, reasoning_tokens, status, max_output_tokens):
    """One durable receipt per selection response (kept across job segments). An incomplete
    response halves the next batch; a single candidate that cannot be answered completely is
    an explicit block, never a silent loop. Nothing of an incomplete response is applied."""
    record = selection_output(state)
    requested = len(requested_ids)
    record["receipts"] = [*record.get("receipts", []), {"status": status, "requested": requested, "returned": returned,
        "output_tokens": output_tokens, "reasoning_tokens": reasoning_tokens, "max_output_tokens": max_output_tokens,
        "article_ids": list(requested_ids)[:20], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}][-12:]
    if status == "completed":
        if record.get("cap") and output_tokens < max_output_tokens*0.6:
            # Measured room again: let the estimate decide, one step at a time, below any size already cut.
            record["cap"] = min(record["cap"] + max(1, record["cap"]//2), (record.get("ceiling") or 10**6)-1)
        record.pop("blocked", None)
        return {"blocked": False, "next_size": selection_batch_size(state, max_output_tokens)}
    record["cap"] = max(1, requested//2)
    record["ceiling"] = min(record.get("ceiling") or requested, requested)
    if requested <= 1:
        record["blocked"] = {"article_ids": list(requested_ids), "output_tokens": output_tokens,
            "max_output_tokens": max_output_tokens, "at": record["receipts"][-1]["at"]}
        return {"blocked": True, "next_size": 0}
    record["failed_batches"] = [*record.get("failed_batches", []), {"article_ids": list(requested_ids), "size": requested}][-6:]
    return {"blocked": False, "next_size": selection_batch_size(state, max_output_tokens)}


def selection_batch(state, candidates, maximum=20, max_output_tokens=None):
    """One selection request: the incumbents as comparison context (their unchanged assessments are
    not regenerated) and the candidates that need a decision, as many as one complete response holds.
    A batch that was truncated is never sent again with the same size; the rest waits its turn."""
    finalists = [aid for aid in state.get("evaluation", {}).get("recommended_article_ids", [])
        if aid in state.get("articles", {}) and state["articles"][aid].get("selection_override") is not False and aid not in state.get("held_article_ids", [])]
    target = state.get("research_progress", {}).get("requested_count") or 5
    incumbents = list(dict.fromkeys(finalists))[:min(target, maximum-1)]
    needed = list(dict.fromkeys(candidates))
    size = selection_batch_size(state, max_output_tokens, maximum) if max_output_tokens else maximum
    fresh_room = max(1, maximum-len(incumbents))
    evaluation, fresh = [], 0
    for aid in needed:
        if len(evaluation) >= size:
            break
        if aid not in incumbents:
            if fresh >= fresh_room:
                continue
            fresh += 1
        evaluation.append(aid)
    ids = [*incumbents, *[aid for aid in evaluation if aid not in incumbents]]
    return {"candidate_ids": ids, "evaluation_ids": evaluation, "size": size,
        "deferred_ids": [aid for aid in needed if aid not in evaluation]}


def discovery_signature(state, legacy=False):
    # Reading a body does not erase a discovery checkpoint. New candidates,
    # information resolving a pending notice, or a changed top do invalidate it.
    rows = state.get("articles", {})
    value = [legacy_need_signature(state) if legacy else need_signature(state), state.get("evaluation", {}).get("recommended_article_ids", []),
        {aid: [a.get("abstract"), (a.get("assessment") or {}).get("decision"), a.get("selection_override")]
            for aid, a in sorted(rows.items())},
        [(p.get("key"), len(p.get("rows", []))) for p in state.get("research_pending_pages", [])]]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def discovery_current(state):
    """The last discovery review still describes the corpus. A negative review whose requested actions are all
    done is consumed even when they brought nothing new (run d964ec6e: an inspection added no abstract, the
    signature did not change, and the old « not ready » review sent the research to a partial stop without
    any reading). The next step is then reassessed by a new review; the same action is not admitted twice."""
    progress = state.get("research_progress", {})
    receipt = progress.get("discovery_review", {})
    if receipt.get("signature") not in {discovery_signature(state), discovery_signature(state, legacy=True)}:
        return False
    accepted = set(receipt.get("accepted_actions") or [])
    if not receipt.get("ready_for_reading") and accepted:
        requested = [a for a in progress.get("actions", []) if a.get("key") in accepted]
        if requested and all(a.get("status") != "pending" for a in requested):
            return False
    return True


def network_actions_used(history):
    return sum(not a.get("local", False) and a.get("discovery_checkpoint", False) for a in history)


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
        same_inspection = next((a for a in reversed(history) if action.kind == "inspect" and a.get("kind") == "inspect"
            and sorted(a.get("article_ids", [])) == operation["article_ids"] and " ".join(str(a.get("query", "")).casefold().split())
            == operation["query"] and a.get("status") == "done"), None)
        if same_inspection and isinstance(same_inspection.get("result"), dict) and                 not same_inspection["result"].get("abstracts_enriched") and not same_inspection["result"].get("abstracts_missing"):
            # The same local notices were already inspected and nothing could be added (run d964ec6e); their
            # page size or source list does not make a new inspection.
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
        # Only the network actions queued by discovery/review checkpoints count against their budget: the searches
        # of the need's initial family map (also run as actions) must not consume the complementary discovery.
        used = network_actions_used(history)
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
    gate = reading_gate(state)
    coverage = discovery_coverage(state)
    rows = state.get("articles", {})
    # A representative sample of what each family really returned (titles and the model's own decisions),
    # so that the approaches actually present, new terminology and gaps are seen in the results, not assumed
    # from the queries sent. Retrieval order only; no score.
    by_family = {}
    for search in state.get("recent_searches", []):
        for aid in search.get("article_ids", []):
            by_family.setdefault(_norm_query(search.get("family")) or _norm_query(search.get("query")), []).append(aid)
    observed = []
    for row in coverage:
        ids = list(dict.fromkeys([*by_family.get(_norm_query(row["family"]), []), *by_family.get(_norm_query(row["query"]), [])]))
        ids = [aid for aid in ids if aid in rows and rows[aid].get("selection_override") is not False]
        decision = lambda aid: (rows[aid].get("assessment") or {}).get("decision")
        ids.sort(key=lambda aid: {"retain": 0, None: 1, "pending": 2, "reject": 3}.get(decision(aid), 1))
        observed.append({"family": row["family"], "origin": row["origin"], "sample": [{"id": aid,
            "title": (rows[aid].get("title") or "")[:110], "year": rows[aid].get("year"), "decision": decision(aid)}
            for aid in ids[:5]]})
    history = progress.get("actions", [])
    network_used = network_actions_used(history)
    complementary = [{"family": row["family"], "query": row["query"], "results": row["results"], "judged": row["judged"],
        "retained": row["retained"], "finalists": row["finalists"]} for row in coverage if row["origin"] == "complementary"]
    return {"families_requested": state.get("research_state", {}).get("search_axes", []),
        "remaining_axes": state.get("evaluation", {}).get("remaining_axes", []),
        "finalists": [scientific_dossier(state, aid) for aid in top[:10] if aid in state.get("articles", {})],
        "pending_candidates": [scientific_dossier(state, aid) for aid in pending[:8]], "pending_total": len(pending),
        "challengers": [scientific_dossier(state, aid) for aid in competitors[:8]], "challengers_total": len(competitors),
        "unresolved_ranking_proposals": {aid: proposals[aid] for aid in competitors[:8] if aid in proposals},
        "options": exploration_options(state), "reference_leads": leads[:8], "reference_leads_total": len(leads),
        "previous_review": {k: v for k, v in (progress.get("discovery_review") or {}).items() if k != "coverage"} or None,
        "coverage": coverage, "observed_results": observed, "complementary_yield": complementary,
        "discovery_budget": {"network_actions_used": network_used, "reviews_done": len(progress.get("discovery_reviews", [])),
            "reviews_max_before_reading": MAX_DISCOVERY_REVIEWS},
        "reading_gate": {k: (v[:20] if k == "deferred_ids" else v)
            for k, v in gate.items()}, "deferred_total": len(gate["deferred_ids"]),
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
    "coverage distingue, par famille, recherche exécutée, résultats obtenus et candidats réellement retenus. "
    "Une famille missing porte sa requête (families[].query) : elle est lancée comme recherche complémentaire. "
    "Découverte adaptative : lire observed_results (ce que chaque famille a réellement renvoyé) pour repérer les "
    "approches effectivement présentes, celles que la carte initiale n'avait pas prévues, la terminologie employée par "
    "les travaux pertinents et les familles sans candidat retenu. Une famille n'est pas couverte parce qu'une requête a "
    "été envoyée. Compléter seulement si un résultat observé le justifie : requête reformulée avec la terminologie "
    "observée (pas les mêmes mots réordonnés), page suivante d'une requête à bon rendement (options.next_pages, offset), "
    "références/citations d'un finaliste pertinent (similar, mode references/citations). complementary_yield donne le "
    "rendement des compléments déjà faits : ne plus en proposer quand les derniers n'ont rien retenu ou quand "
    "discovery_budget est épuisé ; donner alors ready_for_reading ou stop_reason. "
    "Si reading_gate.open est vrai, ready_for_reading peut être accordé alors que des notices restent non examinées : "
    "elles sont reportées (ni retenues ni rejetées) et examinées après les lectures initiales des finalistes. "
    "Les actions proposées sont exécutées automatiquement par le système avant toute lecture ; la lecture des textes "
    "intégraux des finalistes est automatique après ready_for_reading=true : inspect ne sert qu'à récupérer des notices. "
    "Une référence citée n'est pas une publication vérifiée. ready_for_reading autorise seulement la "
    "lecture des meilleurs candidats provisoires, jamais la complétude scientifique. "
    "stop_reason arrête la recherche SANS lire la présélection : le réserver à un blocage scientifique justifié (aucun "
    "candidat exploitable). Si la présélection mérite d'être lue malgré des lacunes et qu'aucune action nouvelle utile "
    "n'est réalisable, donner ready_for_reading=true avec les lacunes dans gaps ; ne pas répéter la revue inchangée."
)


def save_discovery_review(state, args, maximum_network=3):
    # Ready with actions (real trial d4789a0e: gpt-6-luna asked to read the finalists' full texts with « inspect »
    # and to check one competitor): no refusal. The actions are queued and run first (transition() gives pending
    # actions priority); if they change the corpus this review is no longer current and a new one is asked, so
    # reading never starts on a preselection the actions would have changed. Refusing made the model believe it
    # had to run them itself and stop.
    pool = triage_pool(state, maximum=10**6)
    gate = reading_gate(state, pool)
    rows = state.get("articles", {})
    progress = state.setdefault("research_progress", {})
    adjustments = []
    # Families citing works outside the corpus, or « covered » without a candidate: corrected and reported, not refused
    # (a refusal the model repeats unchanged is a loop: real trial 442d1365, nine refused reviews in a row).
    for family in args.families:
        unknown = [aid for aid in family.article_ids if aid not in rows]
        if unknown:
            family.article_ids = [aid for aid in family.article_ids if aid in rows]
            adjustments.append(f"famille « {family.family[:80]} » : identifiants hors corpus ignorés {unknown[:4]}")
        if family.status == "covered" and not family.article_ids:
            family.status = "searched"
            adjustments.append(f"famille « {family.family[:80]} » : covered sans candidat → searched")
    # A family declared missing while naming notices already in the corpus but not judged yet: the model has spotted,
    # in the results, works that may answer better (real trial 442d1365: TetGAN, Neural Volumetric Mesh Generator,
    # DDPM-Polycube). Their evaluation is what it asks for: it is queued (local), before any reading.
    named = [aid for family in args.families if family.status == "missing" for aid in family.article_ids
        if (rows[aid].get("assessment") or {}).get("decision") not in {"retain", "reject"}
        and rows[aid].get("selection_override") is not False and aid not in state.get("held_article_ids", [])]
    implied = [ResearchAction(kind="triage", article_ids=list(dict.fromkeys(named))[:20],
        reason="Candidats repérés par la revue de découverte dans les résultats, encore non évalués.")] if named else []
    # A missing family with its own query (written by the model from what it observed): the complementary search it
    # calls for, within the bounded network budget (real trial ba8cf8d9: a family declared missing three times, never
    # searched, the reviews only re-inspecting stored notices).
    proposed = {_norm_query(a.query) for a in args.actions if a.kind == "search"}
    implied += [ResearchAction(kind="search", query=family.query.strip(), family=family.family[:160],
        reason="Famille déclarée manquante par la revue de découverte : " + family.reason[:300])
        for family in args.families if family.status == "missing" and family.query.strip()
        and _norm_query(family.query) not in proposed]
    problems = []
    if args.ready_for_reading:
        # Unjudged notices alone no longer block reading (run 9c7625c8); an unestablished preselection does.
        # Every reason is given at once, so one review can address them all.
        ranked = canonical_selection(state)["ranked_ids"]
        reasons = [*(gate["reasons"] if pool and not gate["open"] else []),
            # Only an outdated finalist judgment blocks the reading of the preselection (same rule as reading_gate).
            *([] if not set(stale_judged(state) if pool else stale_ranking_candidates(state)) & set(ranked)
                else ["classement à réviser"]),
            *([] if state.get("evaluation", {}).get("recommended_article_ids") else ["aucun finaliste retenu"])]
        if reasons:
            problems.append("Présélection non établie : " + "; ".join(dict.fromkeys(reasons[:8])) + ". Les notices non "
                "examinées ne sont ni retenues ni rejetées.")
    network_left = maximum_network is None or network_actions_used(progress.get("actions", [])) < maximum_network
    if (args.ready_for_reading and any(f.status == "missing" for f in args.families) and network_left
            and not implied and not args.actions):
        # Once the complementary discovery budget is spent, a missing family no longer forbids reading the
        # provisional preselection: the gap is kept and reported, the research is partial on that point.
        problems.append("Famille manquante : proposer une exploration ciblée (search, similar, triage des notices "
            "repérées) ou un arrêt partiel explicite.")
    declared = {_norm_query(f.family): f.status for f in args.families}
    unexplored = [f["family"] for f in planned_families(state) if not f["searched"]
        and declared.get(_norm_query(f["family"])) not in {"not_relevant", "unavailable", "covered"}]
    if args.ready_for_reading and unexplored:
        # A family of the need's own map never searched cannot be skipped silently before reading.
        problems.append("Familles planifiées jamais interrogées : " + "; ".join(unexplored[:6]) + ". Lancer leur requête "
            "(action search) ou les déclarer not_relevant/unavailable/covered avec leur raison.")
    if problems:
        refusals = progress.setdefault("discovery_refusals", {})
        key = discovery_signature(state)
        refusals[key] = refusals.get(key, 0) + 1
        if refusals[key] <= MAX_DISCOVERY_REFUSALS:
            raise ValueError(" ".join(problems))
        # The same objection on the same state a third time: recorded as a not-ready review with the objection as a gap
        # (transition() then reads the provisional preselection if it is readable, or keeps triaging), never a loop.
        args = args.model_copy(update={"ready_for_reading": False, "gaps": [*args.gaps, *problems][:12]})
        adjustments.append("revue non prête après refus répétés : " + " ".join(problems)[:300])
    actions = enqueue_actions(state, [*implied, *args.actions], maximum_network=maximum_network)
    row = {**args.model_dump(), "signature": discovery_signature(state), "accepted_actions": [a["key"] for a in actions],
        "reference_inventory_signature": reference_inventory_signature(state),
        "coverage": discovery_coverage(state)}
    if args.ready_for_reading and pool:
        # Explicit policy for what was not judged before reading: deferred, neither retained nor rejected,
        # judged after the initial readings of the finalists (and able to replace one of them).
        row["deferred_candidates"] = {"count": len(pool), "article_ids": pool[:40],
            "policy": "Notices non examinées avant la lecture des finalistes provisoires : ni retenues ni rejetées ; "
                "examinées après les lectures initiales, elles peuvent remplacer un finaliste par comparaison justifiée."}
    row["stop_origin"] = "model" if args.stop_reason else None
    if adjustments:
        row["adjustments"] = adjustments
    not_admitted = len(implied) + len(args.actions) - len(actions)
    if not_admitted:
        # Visible, not silent: duplicated or over-budget actions were not queued.
        row["actions_not_admitted"] = {"count": not_admitted, "network_budget_left": network_left}
    if not args.ready_for_reading and not actions and not pending_actions(state) and not args.stop_reason:
        # A system closure (nothing new admissible): the provisional preselection is read (transition()); only the
        # model's own justified stop_reason stops the research before reading.
        row["stop_reason"] = "Aucune nouvelle exploration utile admise dans le budget de découverte ; couverture partielle."
        row["stop_origin"] = "system"
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
    # 1. Discovery coverage before triage volume: a family of the need's map never searched gets its
    # own query now, whatever the number of notices still to judge (run 9c7625c8).
    family = next_planned_family(state) if state.get("recent_searches") else None
    if family:
        added = enqueue_actions(state, [ResearchAction(kind="search", query=family["query"], family=family["family"][:160],
            reason="Famille d'approches planifiée par research_need, encore jamais interrogée.")])
        if added:
            return {"stage": "action", "action": added[0], "reason": "planned_family"}
    # 2. Triage until the preselection is established; the rest is deferred, never rejected.
    pool = triage_pool(state, maximum=10**6)
    candidates = pool[:16]
    gate = reading_gate(state, pool)
    if not pool or gate["open"]:
        # Notices already fetched but held back by the new-candidate share are admitted before any reading,
        # from the local page, without a provider request (run d964ec6e: a relevant work stayed in
        # research_pending_pages, never judged). The unchanged limits still apply.
        for page in [p for p in state.get("research_pending_pages", []) if p.get("rows") and len(p.get("key", [])) == 4]:
            query, sources, offset, limit = page["key"]
            family = next((s.get("family") for s in state.get("recent_searches", []) if s.get("key") == page["key"]), None) or LOCAL_RESERVE_FAMILY
            added = enqueue_actions(state, [ResearchAction(kind="search", query=query, sources=sources, offset=offset,
                limit_per_source=limit, family=family[:160], reason="Notices déjà reçues du fournisseur, non encore enregistrées.")])
            if added:
                return {"stage": "action", "action": added[0], "reason": "held_notices"}
    if candidates and not gate["open"]:
        # Before reading, only outdated finalists' judgments hold reading back (reading_gate).
        ranked = canonical_selection(state)["ranked_ids"]
        stale = [aid for aid in stale_judged(state) if aid in ranked]
        if stale and gate["reasons"] == ["classement à réviser"]:
            # Only judged candidates whose notice or card changed (a reading): revised alone, so that the
            # deferred notices wait for their turn instead of reopening the discovery review each time.
            return {"stage": "rerank", "article_ids": stale[:16], "waiting_for": gate["reasons"]}
        # What reading still waits for is traced, not only the number of notices left.
        return {"stage": "triage", "article_ids": list(dict.fromkeys([*stale, *candidates]))[:16],
            "waiting_for": gate["reasons"][:4]}
    selected = canonical_selection(state)["ranked_ids"]
    if not selected and not state.get("recent_searches"):
        return {"stage": "discover"}
    read_done = bool(selected) and initial_readings_done(state, selected)
    # 3. Same-tier challengers are compared with the weakest finalists once the finalists' initial readings
    # exist: the comparison then uses what the readings established, and it never delays the first readings
    # (real trial d4789a0e: ten same-tier comparisons before any reading).
    if read_done and not stale_judged(state):
        pairs = same_tier_reviews(state)
        if pairs:
            return {"stage": "rerank", "article_ids": [p["challenger_id"] for p in pairs], "pair_reviews": pairs}
    deferred_policy = (state.get("research_progress", {}).get("discovery_review") or {}).get("deferred_candidates")
    if candidates and deferred_policy and read_done and deferred_triage_productive(state):
        # 4. Deferred notices are judged once the finalists' initial readings exist, without a discovery
        # review after every batch; a better one found then becomes a finalist and is read in turn. Bounded by
        # its marginal yield (deferred_triage_productive): it goes on while batches still retain works.
        state.setdefault("research_progress", {}).setdefault("deferred_triage", []).append(list(candidates))
        return {"stage": "triage", "article_ids": candidates, "deferred": True}
    reviews_done = len(state.get("research_progress", {}).get("discovery_reviews", []))
    # The discovery review is a pre-reading checkpoint: once the initial readings exist, decisions taken later (deferred
    # triage, revisions) do not ask for another one; outdated rankings are revised below (ranking_current).
    if not discovery_current(state) and not read_done and not (selected and reviews_done >= MAX_DISCOVERY_REVIEWS
            and gate["open"] and not pending_actions(state)):
        # Before reading, only an outdated FINALIST judgment is revised here; the others wait for the readings.
        stale = [aid for aid in stale_judged(state) if aid in selected] if not read_done else stale_judged(state)
        if selected and stale:
            return {"stage": "rerank", "article_ids": stale[:16]}
        return {"stage": "discovery_review"}
    discovery = state.get("research_progress", {}).get("discovery_review", {})
    if not selected:
        if not discovery.get("ready_for_reading") and discovery_current(state):
            return {"stage": "partial", "reason": discovery.get("stop_reason") or "discovery_incomplete"}
        return {"stage": "review" if state.get("recent_searches") else "discover"}
    if not read_done:
        if not discovery.get("ready_for_reading"):
            # Real trial 02098ebc / run d964ec6e: a « not ready » review with no admissible action left (network
            # budget spent, nothing new to do) used to end the research without reading anything. The provisional
            # preselection is read instead; the review's gaps stay recorded and the coverage is reported partial.
            state.setdefault("research_progress", {})["discovery_closed"] = {
                "reason": discovery.get("stop_reason") or ("discovery_review_limit" if reviews_done >= MAX_DISCOVERY_REVIEWS
                    else "discovery_incomplete"), "gaps": discovery.get("gaps", []), "reviews": reviews_done}
            return {"stage": "read", "reason": "provisional_preselection"}
        return {"stage": "read"}
    if state.get("research_progress", {}).get("reference_leads") and discovery.get("reference_inventory_signature") != reference_inventory_signature(state):
        return {"stage": "discovery_review", "reason": "new_local_bibliographic_leads"}
    if len(observed["verified_analyzed_ids"]) >= 2 and not comparison_current(state):
        return {"stage": "compare"}
    if not ranking_current(state):
        return {"stage": "rerank", "article_ids": stale_ranking_candidates(state)[:16]}
    return {"stage": "review"}


def initial_readings_done(state, selected):
    """Every provisional finalist has its initial reading (or a recorded reason it cannot have one).
    A coarse reading checkpoint requests a semantic review; it does not assert depth."""
    by_id = {t["article_id"]: t for t in state.get("scientific_reading", {}).get("tasks", [])}
    return bool(state.get("scientific_reading", {}).get("blocked_reason")) or all(aid in by_id and (
        by_id[aid].get("initial_reading_ready") or by_id[aid].get("exhausted") or
        by_id[aid].get("status") in {"source_unavailable", "needs_review"}) for aid in selected)


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
        if len(key) != 4 or search.get("kind"):
            # Citation/reference explorations have no provider page to continue.
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
        "planned_families": [{k: v for k, v in f.items() if k != "attempted"} for f in planned_families(state)],
        "families_searched": sorted({s.get("family") for s in searches if s.get("family")}),
        "queries_collected": [s.get("query") for s in searches][-12:],
        "web_search_fallback": bool(unavailable) or not searches}


def _norm_query(text):
    return " ".join(str(text or "").casefold().split())


def approach_links(state, query="", family=None, approach_ids=()):
    """Associate only explicit IDs, exact labels/queries or an already linked query.

    No translation or loose lexical overlap is treated as scientific coverage.
    """
    axes = {"approach_"+hashlib.sha256(_norm_query(a).encode()).hexdigest()[:12]: a
        for a in state.get("research_state", {}).get("search_axes", [])[:12]}
    if set(approach_ids)-axes.keys():
        raise ValueError("Approche inconnue : reprendre les identifiants de approach_coverage.")
    linked = set(approach_ids)
    for aid, label in axes.items():
        if _norm_query(label) in {_norm_query(family), _norm_query(query)}:
            linked.add(aid)
    for search in state.get("recent_searches", []):
        if query and _norm_query(search.get("query")) == _norm_query(query):
            linked.update(set(search.get("approach_ids") or []) & axes.keys())
            linked.update(aid for aid, label in axes.items() if _norm_query(search.get("family")) == _norm_query(label))
    return sorted(linked), (axes[next(iter(linked))] if len(linked) == 1 else family)


def approach_coverage(state):
    """Factual coverage of the model's own axes; no scientific score or controller.

    Exact family labels or explicit approach IDs link searches. Lexical similarity
    is deliberately not a proof that a scientific approach has been explored.
    """
    axes = list(dict.fromkeys(state.get("research_state", {}).get("search_axes", [])))[:12]
    searches = state.get("recent_searches", [])
    rows = state.get("articles", {})
    examined = set(state.get("examined_article_ids", []))
    result = []
    for axis in axes:
        aid = "approach_" + hashlib.sha256(_norm_query(axis).encode()).hexdigest()[:12]
        own = [s for s in searches if aid in (s.get("approach_ids") or []) or
            _norm_query(s.get("family")) == _norm_query(axis)]
        ids = {i for s in own for i in s.get("article_ids", []) if i in rows}
        ids.update(i for i, a in rows.items() if aid in (a.get("assessment") or {}).get("approach_ids", []))
        providers = {p for s in own for p, r in (s.get("providers") or {}).items()
            if isinstance(r.get("returned"), int)}
        local = any(s.get("kind") == "local_memory" for s in own)
        failed = {w.get("source") for s in own for w in s.get("warnings", []) if w.get("source")}
        judged = [i for i in ids if i in examined and (rows[i].get("assessment") or {}).get("decision")]
        retained = [i for i in judged if rows[i]["assessment"].get("decision") == "retain"]
        # A current explicit association made while judging a stored candidate
        # establishes local exploration, even if the earlier query was generic.
        status = ("relevant_found" if retained else "not_searched" if not own else "no_provider_answer" if not providers and not local else
            "no_results" if not ids else "results_unexamined" if not judged else
            "relevant_found" if retained else "no_relevant_found")
        result.append({"id": aid, "family": axis[:160], "status": status, "searches": len(own),
            "providers_answered": sorted(providers), "providers_failed": sorted(failed),
            "collected": len(ids), "examined": len(ids & examined), "judged": len(judged),
            "presented": len(ids & set(state.get("exposed_article_ids", []))),
            "retained": len(retained), "unexamined": len(ids - examined),
            "potential_gap": status != "relevant_found"})
    return result


def discovery_attention(state):
    """Bounded factual priorities for the existing conversation, never an executor."""
    coverage = approach_coverage(state)
    rows, exposed = state.get("articles", {}), set(state.get("exposed_article_ids", []))
    examined = set(state.get("examined_article_ids", []))
    candidates = {}
    for search in state.get("recent_searches", []):
        for item in search.get("candidate_index", []):
            candidates.setdefault(item["id"], item)
    candidates.update({aid: {"id": aid, "title": a["title"][:180], "analysis_available": bool(a.get("analysis"))}
        for aid, a in rows.items()})
    pending = [{**c, "retrieved": aid in rows, "presented": aid in exposed, "evaluated": False}
        for aid, c in candidates.items() if aid not in examined and
        rows.get(aid, {}).get("selection_override") is not False and aid not in state.get("held_article_ids", [])]
    pending.sort(key=lambda c: (not c.get("analysis_available", False), not c["retrieved"]))
    gaps = [{"id": r["id"], "family": r["family"], "status": r["status"]} for r in coverage if r["potential_gap"]]
    unlinked = [s for s in state.get("recent_searches", []) if s.get("kind") == "local_memory" and
        not approach_links(state, s.get("query", ""), s.get("family"))[0]]
    return {"approaches": coverage, "gaps": gaps, "unevaluated_candidates": pending[:24],
        "unevaluated_total": len(pending), "unlinked_memory_queries": [s["query"] for s in unlinked][-6:],
        "priority": "compare_stored_candidates" if pending else "target_unexplored_approach" if gaps else "synthesize",
        "guidance": "Évaluer les candidats conservés avant un nouvel appel fournisseur ; pertinence à juger, non présumée. "
            "Relier les consultations aux approach_ids seulement si scientifiquement justifié. Compléter les lacunes "
            "dans les tours restants ; sinon publier explicitement un résultat partiel."}


def planned_families(state):
    """The need's family map against the stored searches: a family counts as explored once
    its own query (or a search labelled with its name) was collected, whatever the provider."""
    searches = state.get("recent_searches", [])
    done_queries = {_norm_query(s.get("query")) for s in searches}
    done_families = {_norm_query(s.get("family")) for s in searches if s.get("family")}
    attempted = {(_norm_query(a.get("query")), _norm_query(a.get("family"))) for a in
        state.get("research_progress", {}).get("actions", []) if a.get("kind") == "search" and a.get("status") != "pending"}
    return [{"family": f["family"], "query": f["query"],
        "searched": _norm_query(f["query"]) in done_queries or _norm_query(f["family"]) in done_families,
        # A search action already executed for it (even refused by a budget) is never scheduled again.
        "attempted": any(q == _norm_query(f["query"]) or fam == _norm_query(f["family"]) for q, fam in attempted)}
        for f in (state.get("research_need") or {}).get("families", [])]


# Leading notices of each searched family judged before long readings may start (round-robin triage
# reaches them first); the rest of a large collection is deferred, never rejected.
MIN_JUDGED_PER_FAMILY = 3
# Bounded pre-reading triage: once this many notices per requested work are judged, a preselection without
# enough direct works is read anyway (provisionally, with its deferred notices judged after the readings).
PRE_READING_JUDGED_PER_TARGET = 6
# Discovery reviews before the first readings: each may queue at most three actions; past this bound a ready
# preselection is read with the coverage recorded as partial.
MAX_DISCOVERY_REVIEWS = 4
# Deferred triage after the initial readings stops once this many consecutive batches retained nothing
# (marginal yield), or after the safety bound of batches; the notices left stay deferred, reported unexamined.
DEFERRED_BARREN_BATCHES = 2
MAX_DEFERRED_TRIAGE_ROUNDS = 12


def deferred_triage_productive(state):
    """Marginal yield of the deferred triage: the batches already sent, judged by the model's own decisions on
    them. Replay 9c7625c8: relevant works were among the deferred notices (a fixed small bound lost them);
    real trial 02098ebc: dozens of batches retained little. Measured, not guessed."""
    rounds = (state.get("research_progress") or {}).get("deferred_triage", [])
    if len(rounds) >= MAX_DEFERRED_TRIAGE_ROUNDS:
        return False
    rows = state.get("articles", {})
    retained = [sum((rows.get(aid, {}).get("assessment") or {}).get("decision") == "retain" for aid in ids) for ids in rounds]
    return not (len(retained) >= DEFERRED_BARREN_BATCHES and not any(retained[-DEFERRED_BARREN_BATCHES:]))
# Identical objections to a discovery review on an unchanged state before it is recorded as not ready.
MAX_DISCOVERY_REFUSALS = 2
# Label of the local re-admission of notices already received (not a new family of approaches).
LOCAL_RESERVE_FAMILY = "réserve locale"


def discovery_coverage(state):
    """Coverage of the need's family map, kept apart from the triage volume. For each planned family:
    the searches really executed, the providers that answered, the notices obtained (stored or held in
    a provider page), how many were judged and retained. A search executed is not results obtained,
    and results obtained are not relevance found: the observed state says which one holds. The
    model's own declarations (covered, searched, missing, unavailable, not_relevant) are kept beside it.
    Complementary searches decided during the research (discovery or review actions, new formulations,
    next pages, citations) get their own rows (origin « complementary »), measured the same way: their
    yield (results, judged, retained) is what tells whether more discovery is still worth it."""
    rows = state.get("articles", {})
    searches = state.get("recent_searches", [])
    finalists = set(state.get("evaluation", {}).get("recommended_article_ids", []))
    review = state.get("research_progress", {}).get("discovery_review") or {}
    declared = {_norm_query(f.get("family")): f.get("status") for f in review.get("families", [])}
    actions = state.get("research_progress", {}).get("actions", [])

    def measure(label, query, own, origin, extra_ids=()):
        answered = sorted({p for s in own for p, row in (s.get("providers") or {}).items()
            if isinstance(row, dict) and isinstance(row.get("returned"), int)})
        failed = sorted({p for s in own for p, row in (s.get("providers") or {}).items()} - set(answered))
        ids = list(dict.fromkeys([*(aid for s in own for aid in s.get("article_ids", [])), *extra_ids]))
        ids = [aid for aid in ids if aid in rows and rows[aid].get("selection_override") is not False]
        keys = [s.get("key") for s in own]
        held = sum(len(p.get("rows", [])) for p in state.get("research_pending_pages", []) if p.get("key") in keys)
        decisions = {aid: (rows[aid].get("assessment") or {}).get("decision") for aid in ids}
        judged = [aid for aid, d in decisions.items() if d in {"retain", "reject"}]
        examined = [aid for aid, d in decisions.items() if d]
        retained = [aid for aid, d in decisions.items() if d == "retain"]
        name, norm = _norm_query(label), _norm_query(query)
        refused = [a for a in actions if a.get("kind") == "search"
            and (_norm_query(a.get("query")) == norm or _norm_query(a.get("family")) == name)
            and isinstance(a.get("result"), dict) and (a["result"].get("refused") or a["result"].get("warning"))
            and not a["result"].get("collected")]
        status = declared.get(name)
        observed = (status if status in {"not_relevant", "unavailable", "covered"} and not own else
            "not_searched" if not own and not refused else "search_refused" if not own else
            "no_provider_answer" if not answered else "no_results" if not ids and not held else
            "relevant_found" if retained else "results_unjudged" if len(examined) < len(ids) else "no_relevant_found")
        return {"family": label, "query": query, "origin": origin, "observed": observed, "declared": status,
            "searches": len(own), "providers_answered": answered, "providers_failed": failed,
            "results": len(ids), "held_in_provider_pages": held, "examined": len(examined), "judged": len(judged),
            "retained": len(retained),
            "finalists": len([aid for aid in retained if aid in finalists]),
            "needs_complementary_search": observed in {"no_results", "no_provider_answer", "no_relevant_found"}}

    result, used = [], set()
    for family in planned_families(state):
        name, query = _norm_query(family["family"]), _norm_query(family["query"])
        own = [s for s in searches if _norm_query(s.get("query")) == query or _norm_query(s.get("family")) == name]
        used.update(id(s) for s in own)
        tagged = [aid for aid, a in rows.items() if name in {_norm_query(x) for x in a.get("scientific_families") or []}]
        result.append(measure(family["family"], family["query"], own, "planned", tagged))
    complementary = {}
    for search in searches:
        if id(search) in used:
            continue
        local = _norm_query(search.get("family")) in {"", _norm_query(LOCAL_RESERVE_FAMILY)}
        label = search.get("query") if local else search.get("family")
        complementary.setdefault(_norm_query(label), (label, search.get("query"), []))[2].append(search)
    for label, query, own in complementary.values():
        result.append(measure(str(label or "")[:160], query or "", own, "complementary"))
    return result


def next_planned_family(state):
    """A family of the need's map never searched, never attempted and not declared irrelevant or
    unavailable: its own query is due before more triage (run 9c7625c8: 3 of 5 families searched,
    then every model call went to triage and the review that checks families was never reached)."""
    review = state.get("research_progress", {}).get("discovery_review") or {}
    declared = {_norm_query(f.get("family")): f.get("status") for f in review.get("families", [])}
    return next((f for f in planned_families(state) if not f["searched"] and not f["attempted"]
        and declared.get(_norm_query(f["family"])) not in {"not_relevant", "unavailable", "covered"}), None)


def reading_gate(state, pool=None):
    """Whether long readings of the provisional finalists may start while notices remain unjudged.
    Distinct from « every notice triaged »: the need's families are all searched (or declared), the
    leading notices of each family were judged, the finalists fill the requested count, their ranking
    is current, and no selection response is blocked. The remaining notices are deferred: judged
    after the initial readings, never rejected implicitly. Without a structured need (older sessions)
    the previous rule holds: every notice is judged first."""
    pool = triage_pool(state, maximum=10**6) if pool is None else pool
    reasons = []
    if not (state.get("research_need") or {}).get("families"):
        return {"open": not pool, "reasons": ["triage_incomplete"] if pool else [], "deferred_ids": []}
    coverage = discovery_coverage(state)
    missing = [row["family"] for row in coverage if row["observed"] == "not_searched"]
    if missing:
        reasons.append("familles jamais interrogées : " + "; ".join(missing[:6]))
    rows = state.get("articles", {})
    for row in coverage:
        if row["examined"] < min(MIN_JUDGED_PER_FAMILY, row["results"]):
            reasons.append(f"famille « {row['family'][:80]} » : {row['examined']} notice(s) examinée(s) sur {row['results']}")
    target = state.get("research_progress", {}).get("requested_count") or 5
    ranked = canonical_selection(state)["ranked_ids"]
    if len(ranked) < target:
        reasons.append(f"présélection incomplète : {len(ranked)} finaliste(s) pour {target} demandés")
    else:
        # Run d964ec6e: reading allowed with five related/foundational finalists and 50 notices never judged.
        # Real trial 02098ebc: the opposite deadlock — 3 direct finalists of 5, reading refused for ~25 selection
        # calls until the token budget ran out, while reading is precisely what can establish relevance.
        # A provisional preselection is readable when at least half of it answers the need directly (model's own
        # current judgments: tier 0-1), or once a bounded pre-reading triage has been spent. The notices left are
        # deferred (judged after the initial readings, able to replace a finalist), never rejected.
        direct = [aid for aid in ranked[:target] if qualification(state, aid)["tier"] <= 1]
        judged = sum(1 for a in rows.values() if (a.get("assessment") or {}).get("decision") in {"retain", "reject"})
        if len(direct) < -(-target//2) and judged < PRE_READING_JUDGED_PER_TARGET*target:
            reasons.append(f"présélection sans contribution directe suffisante : {len(direct)} finaliste(s) direct(s) "
                f"sur {target} ; notices encore non examinées")
    # Only a finalist whose own judgment is outdated blocks reading; other outdated judgments are revised after
    # the initial readings, with the information those readings bring.
    if set(stale_judged(state)) & set(ranked[:target]):
        reasons.append("classement à réviser")
    if (state.get("research_progress", {}).get("selection_output") or {}).get("blocked"):
        reasons.append("lot de sélection bloqué par la limite de sortie")
    deferred = [aid for aid in pool if aid in rows]
    return {"open": not pool or not reasons, "reasons": reasons, "deferred_ids": deferred}


def same_tier_reviews(state, limit=3):
    """Retained challengers judged in the SAME tier as the weakest removable finalists, never compared
    with them on this evidence (rebalance_ranking only moves a better tier). The model compares the two
    dossiers on the problem treated, the output produced, the role of AI, the object and its dimension,
    the method, the validation and the limits; it replaces only with a justified, traced replacement.
    A pair already reviewed on unchanged notices and need is not asked again, and a work that was
    replaced is not proposed back without new information. Information-poor dossiers are not paired."""
    rows = state.get("articles", {})
    target = state.get("research_progress", {}).get("requested_count")
    current = [aid for aid in state.get("evaluation", {}).get("recommended_article_ids", []) if aid in rows]
    if not target or len(current) < target:
        return []
    held = set(state.get("held_article_ids", []))
    accepted = {aid for aid, a in rows.items() if a.get("selection_override") is True}
    groups = version_groups(state)
    q = {aid: qualification(state, aid) for aid in current}
    removable = [aid for aid in current if aid not in accepted and q[aid]["current"]
        and (rows[aid].get("assessment") or {}).get("relevance")]
    if not removable:
        return []
    tier = max(q[aid]["tier"] for aid in removable)
    if tier >= 3:
        return []
    weakest = [aid for aid in removable if q[aid]["tier"] == tier]
    progress = state.get("research_progress", {})
    reviewed = progress.get("pair_reviews", {})
    replaced = {e.get("outgoing_id") for e in progress.get("ranking_changes", []) if e.get("outgoing_id") and
        all(candidate_signature(state, aid) == version for aid, version in (e.get("dependencies") or {}).items())}
    need = need_signature(state)
    # The best challengers are compared, not every related work (real trial d4789a0e: ten same-tier comparisons
    # took most of a segment): at most the requested count per group of weakest finalists.
    done = sum(1 for r in reviewed.values() if sorted(r.get("finalist_ids", [])) == sorted(weakest))
    limit = max(0, min(limit, (target or 5) - done))
    if not limit:
        return []
    position = {aid: i for i, aid in enumerate(rows)}
    pairs = []
    for aid, a in rows.items():
        assessment = a.get("assessment") or {}
        if (aid in current or aid in held or a.get("selection_override") is False or assessment.get("decision") != "retain"
                or not assessment.get("relevance") or aid in replaced
                or any(groups.get(aid, aid) == groups.get(x, x) for x in current)):
            continue
        qa = qualification(state, aid)
        if not qa["current"] or qa["tier"] != tier:
            continue
        key = hashlib.sha256(json.dumps([aid, sorted(weakest), {x: candidate_signature(state, x) for x in [aid, *weakest]}, need],
            sort_keys=True).encode()).hexdigest()
        if key in reviewed:
            continue
        met = sum(status == "met" for status in qa["essential"].values())
        pairs.append(({"challenger_id": aid, "finalist_ids": weakest, "tier": tier, "label": TIER_LABELS[tier], "key": key},
            (-met, position[aid])))
    return [pair for pair, _ in sorted(pairs, key=lambda p: p[1])][:limit]


COMPARISON_DIMENSIONS = ("problem", "output", "ai_role", "object_scope", "method", "results_validation", "uncertainties")


def record_pair_reviews(state, reviews, changes, unanswered=False):
    """The outcome of each same-tier comparison is kept (replaced, kept, or unanswered when the
    request produced no applicable decision), with its dependencies: the same pair is not asked
    again and the ranking does not change again on the same evidence."""
    progress = state.setdefault("research_progress", {})
    store = progress.setdefault("pair_reviews", {})
    for review in reviews or []:
        event = next((e for e in changes if e.get("incoming_id") == review["challenger_id"]), None)
        store[review["key"]] = {"challenger_id": review["challenger_id"], "finalist_ids": review["finalist_ids"],
            "tier": review["tier"], "outcome": "unanswered" if unanswered else "replaced" if event else "kept",
            "outgoing_id": event.get("outgoing_id") if event else None,
            "justification": (event or {}).get("justification"),
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return store


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
        if not name or warning.get("category") == "local_wait":
            # Our own pacing ran out of time before any request: the provider is not unhealthy.
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
    if reason == "selection_output_blocked":
        # Even a single-candidate selection response was cut by the output limit: a continuation
        # would send the same request again. Explicit stop, decisions already validated kept.
        blocked = ((state.get("research_progress") or {}).get("selection_output") or {}).get("blocked") or {}
        return stop(job, "stalled", error="Recherche arrêtée : même un lot d'un seul candidat ("
            + ", ".join(blocked.get("article_ids", [])[:1]) + f") dépasse la limite de sortie ({blocked.get('max_output_tokens')} "
            "tokens) ; aucune reprise identique n'est lancée. Les décisions déjà validées, le classement et les candidats "
            "sont conservés.", last_cause=reason)
    if observed["complete"]:
        return stop(job, "completed")
    if reason == "research_partial":
        # The control loop found nothing left to do in this state (run d964ec6e: two more segments only
        # finalized the same partial answer again). Deterministic: stop with the cause, nothing repeated.
        cause = ((observed.get("review") or {}).get("stop_reason") or (observed.get("discovery_review") or {}).get("stop_reason")
            or "couverture incomplète")
        return stop(job, "exhausted", error="Recherche partielle : " + str(cause))
    if observed.get("partial_stop"):
        return stop(job, "exhausted", error="Recherche partielle : " + str(observed["review"].get("stop_reason") or
            observed.get("discovery_review", {}).get("stop_reason")))
    if observed["exhausted"]:
        return stop(job, "exhausted", error="Recherche terminée avec couverture partielle : " + str(observed["review"].get("reason")))
    technical = reason in INTERRUPTED or run_status == "failed"
    progressed = sig != job.get("corpus_signature")
    # An interruption after verifiable progress is not a failed segment (real trial db35a824: two segments each
    # ended on a provider read timeout after saving dozens of decisions, and the job was declared stalled).
    # Consecutive interruptions without any progress still stop the job.
    job["technical_failures"] = job.get("technical_failures", 0) + 1 if technical and not progressed else 0
    job["no_progress"] = job.get("no_progress", 0) + 1 if not progressed and not technical else 0
    job["corpus_signature"] = sig
    if job["technical_failures"] >= job["stalled_segments"] or job["no_progress"] >= job["stalled_segments"]:
        return stop(job, "stalled")
    allowed, stop_status = can_start_segment(job)
    if not allowed or reason == "budget_refused":
        return stop(job, stop_status or "budget_exhausted")
    job["status"] = "waiting"
    job["error"] = "Recherche partielle conservée ; poursuite automatique avec le même budget global."
    return job
